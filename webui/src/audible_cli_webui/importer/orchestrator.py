import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from audible import AsyncClient
from audible_cli.cmds.cmd_download import CLIENT_HEADERS, download_aaxc, download_cover
from audible_cli.models import Library
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..audible_bridge import auth_store
from ..crypto import decrypt_bytes
from ..db import SessionLocal
from ..settings import settings
from .abs_client import AbsApiError, AbsClient
from .decrypt import DecryptError, decrypt_aaxc, extract_voucher_credentials
from .targets import missing_setup_reason, resolve_target

logger = logging.getLogger("audible_cli_webui.importer.orchestrator")

# Mirrors the exact response_groups the CLI's own `download` command uses
# (src/audible_cli/cmds/cmd_download.py), so authors/series/cover urls are
# populated the same way.
RESPONSE_GROUPS = "product_desc, media, product_attrs, relationships, series, customer_rights, pdf_url"
COVER_SIZE = "500"
WORKERS_PER_RUN = 3


async def run_import(run_id: int, user_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(models.ImportRun, run_id)
        run.status = models.ImportRunStatus.RUNNING
        run.started_at = datetime.now(UTC)

        # trigger_import already checked this; re-checked in case setup
        # changed between the request and the job starting.
        reason = missing_setup_reason(db, user_id)
        if reason is not None:
            run.status = models.ImportRunStatus.FAILED
            run.error_message = reason
            run.finished_at = datetime.now(UTC)
            db.commit()
            return

        db.commit()
        account = auth_store.get_account(db, user_id)
        abs_settings = db.get(models.AbsSettings, 1)
        library_id, folder_id = resolve_target(db, user_id)
        abs_base_url = abs_settings.base_url
        abs_api_key = decrypt_bytes(abs_settings.encrypted_api_key).decode("utf-8")
        # Built while `account` is still attached to this session — using it
        # after the `with` block closes raises DetachedInstanceError.
        authenticator = auth_store.load_authenticator(account)

    abs_client = AbsClient(abs_base_url, abs_api_key)

    work_dir = Path(settings.work_dir) / "runs" / str(run_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # The CLI attaches CLIENT_HEADERS via @pass_client; without the Audible
        # app User-Agent, the CloudFront CDN serving .aaxc files answers 403.
        async with AsyncClient(auth=authenticator, timeout=30, headers=CLIENT_HEADERS) as api_client:
            library = await Library.from_api_full_sync(
                api_client,
                image_sizes=COVER_SIZE,
                response_groups=RESPONSE_GROUPS,
                status="Active",
            )

            with SessionLocal() as db:
                done_asins = _already_succeeded_asins(db, user_id)

            pending_items = [item for item in library if item.asin not in done_asins]
            skipped_items = [item for item in library if item.asin in done_asins]

            with SessionLocal() as db:
                run = db.get(models.ImportRun, run_id)
                run.total_items = len(pending_items)

                item_ids: dict[str, int] = {}
                for item in pending_items:
                    row = models.ImportRunItem(run_id=run_id, asin=item.asin, title=item.full_title or item.asin)
                    db.add(row)
                    db.flush()
                    item_ids[item.asin] = row.id
                for item in skipped_items:
                    db.add(
                        models.ImportRunItem(
                            run_id=run_id,
                            asin=item.asin,
                            title=item.full_title or item.asin,
                            status=models.ImportRunItemStatus.SKIPPED,
                        )
                    )
                db.commit()

            queue: asyncio.Queue = asyncio.Queue()
            for item in pending_items:
                queue.put_nowait((item, item_ids[item.asin]))

            worker_count = min(WORKERS_PER_RUN, len(pending_items)) or 1
            workers = [
                asyncio.create_task(_worker(queue, api_client.session, work_dir, library_id, folder_id, abs_client))
                for _ in range(worker_count)
            ]
            await queue.join()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

            with SessionLocal() as db:
                account_db = auth_store.get_account(db, user_id)
                if account_db is not None:
                    auth_store.persist_refreshed_tokens(db, account_db, authenticator)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    with SessionLocal() as db:
        items = db.execute(select(models.ImportRunItem).where(models.ImportRunItem.run_id == run_id)).scalars().all()
        failed = [i for i in items if i.status == models.ImportRunItemStatus.FAILED]
        attempted = [i for i in items if i.status != models.ImportRunItemStatus.SKIPPED]

        run = db.get(models.ImportRun, run_id)
        if attempted and len(failed) == len(attempted):
            run.status = models.ImportRunStatus.FAILED
            run.error_message = "All books in this run failed. See individual items for details."
        else:
            # Partial success is still SUCCEEDED at the run level — per-item
            # status already carries the detail; there's no interactive
            # operator here to ask "ignore errors and continue?".
            run.status = models.ImportRunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        db.commit()


def _already_succeeded_asins(db: Session, user_id: int) -> set[str]:
    rows = (
        db.execute(
            select(models.ImportRunItem.asin)
            .join(models.ImportRun, models.ImportRun.id == models.ImportRunItem.run_id)
            .where(
                models.ImportRun.user_id == user_id,
                models.ImportRunItem.status == models.ImportRunItemStatus.SUCCEEDED,
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _worker(
    queue: asyncio.Queue, client, work_dir: Path, library_id: str, folder_id: str, abs_client: AbsClient
) -> None:
    while True:
        item, item_row_id = await queue.get()
        try:
            await _process_item(item, item_row_id, client, work_dir, library_id, folder_id, abs_client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error processing %s", item.asin)
            _set_item_status(item_row_id, models.ImportRunItemStatus.FAILED, error="Unexpected internal error.")
        finally:
            queue.task_done()


async def _process_item(
    item, item_row_id: int, client, work_dir: Path, library_id: str, folder_id: str, abs_client: AbsClient
) -> None:
    item_dir = work_dir / item.asin
    item_dir.mkdir(parents=True, exist_ok=True)
    base_filename = item.asin

    try:
        _set_item_status(item_row_id, models.ImportRunItemStatus.DOWNLOADING)
        # Note: a book requiring Audible's rare multi-part delivery would
        # hit a fallback path in download_aaxc that enqueues onto a
        # module-level queue only ever initialized by the CLI's own `cli()`
        # entrypoint. That's unreachable here by construction, so it
        # surfaces as a clean exception caught below — multi-part books are
        # a deliberate v1 gap, not a silent failure.
        await download_aaxc(
            client=client,
            output_dir=item_dir,
            base_filename=base_filename,
            item=item,
            quality="best",
            overwrite_existing=False,
            filename_mode="asin_only",
            filename_length=230,
        )

        downloaded = _find_downloaded_audio(item_dir, base_filename)
        if downloaded is None:
            raise RuntimeError("Download finished but no audio file was found on disk.")

        if downloaded.suffix == ".mp3":
            # codec "mpeg" means Audible served this one as a plain,
            # already-playable mp3 — no DRM wrapper to strip.
            playable = downloaded
        else:
            _set_item_status(item_row_id, models.ImportRunItemStatus.DECRYPTING)
            key, iv = extract_voucher_credentials(downloaded.with_suffix(".voucher"))
            playable = await decrypt_aaxc(downloaded, key, iv)

        cover_path: Path | None = item_dir / f"{base_filename}_({COVER_SIZE}).jpg"
        try:
            await download_cover(client, item_dir, base_filename, item, COVER_SIZE, overwrite_existing=False)
        except Exception:
            logger.info("Cover download failed for %s; uploading without one.", item.asin)
        if not cover_path.is_file():
            cover_path = None

        _set_item_status(item_row_id, models.ImportRunItemStatus.UPLOADING)
        authors = ", ".join(sorted(a["name"] for a in item.authors)) if item.authors else None
        series = ", ".join(sorted(s["title"] for s in item.series)) if item.series else None

        await abs_client.upload_book(
            library_id=library_id,
            folder_id=folder_id,
            title=item.full_title or item.asin,
            author=authors,
            series=series,
            audio_path=playable,
            cover_path=cover_path,
        )

        _set_item_status(item_row_id, models.ImportRunItemStatus.SUCCEEDED)
    except (DecryptError, AbsApiError) as exc:
        _set_item_status(item_row_id, models.ImportRunItemStatus.FAILED, error=str(exc))
    except Exception as exc:
        logger.exception("Failed to import %s", item.asin)
        _set_item_status(item_row_id, models.ImportRunItemStatus.FAILED, error=str(exc)[:500])
    finally:
        shutil.rmtree(item_dir, ignore_errors=True)


def _find_downloaded_audio(item_dir: Path, base_filename: str) -> Path | None:
    for suffix in (".aaxc", ".mp3"):
        matches = sorted(item_dir.glob(f"{base_filename}-*{suffix}"))
        if matches:
            return matches[0]
    return None


def _set_item_status(item_row_id: int, status: models.ImportRunItemStatus, error: str | None = None) -> None:
    with SessionLocal() as db:
        row = db.get(models.ImportRunItem, item_row_id)
        if row is None:
            return
        row.status = status
        row.error_message = error
        if status == models.ImportRunItemStatus.DOWNLOADING and row.started_at is None:
            row.started_at = datetime.now(UTC)
        if status in (models.ImportRunItemStatus.SUCCEEDED, models.ImportRunItemStatus.FAILED):
            row.finished_at = datetime.now(UTC)
        db.commit()
