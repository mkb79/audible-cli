from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Tuple

import click
import httpx

from audible_cli.config import Session
from audible_cli.decorators import page_size_option, pass_session
from audible_cli.db import AsyncFileLock
from audible_cli.db.async_db_library import (
    delta_import_async,
    ensure_initialized_async,
    ensure_library_schema,
    explain_query_async,
    export_library_async,
    full_import_async,
    get_docs_by_asins,
    get_docs_by_titles,
    get_settings_async,
    init_db_async,
    list_soft_deleted_async,
    list_sync_logs_async,
    open_db,
    query_search_async,
    query_search_fts_async,
    rebuild_fts_async,
)

logger = logging.getLogger(__name__)

HAS_H2 = importlib.util.find_spec("h2") is not None
if not HAS_H2:
    logger.info("h2 module not found. Switching to HTTP/1.1.")

DEFAULT_RESPONSE_GROUPS = (
    "badge_types,is_archived,is_finished,is_playable,is_removable,is_visible,"
    "order_details,origin_asin,percent_complete,shared,ws4v_rights,badges,"
    "category_ladders,category_media,category_metadata,contributors,customer_rights,"
    "media,product_attrs,product_desc,product_details,product_extended_attrs,"
    "product_plans,product_plan_details,profile_sharing,rating,relationships_v2,"
    "sample,sku,pdf_url,series"
)


def make_async_client(session: Session, timeout: httpx.Timeout, limits: httpx.Limits) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with h2 enabled when available.

    Args:
        session: Current CLI session object.
        timeout: httpx timeout configuration.
        limits: httpx connection pool limits.

    Returns:
        Configured AsyncClient.
    """
    return httpx.AsyncClient(
        auth=session.auth,
        timeout=timeout,
        limits=limits,
        http2=HAS_H2,
    )


@click.group(help="Manage the user's library database")
def library() -> None:
    """Click group for library DB commands."""
    # Intentionally empty


@library.command("init", help="Initialize DB for this session user with fixed response_groups")
@click.option("--response-groups", required=True, help="Response groups string used for ALL future requests")
@pass_session
def cmd_init(session: Session, response_groups: str) -> None:
    """Initialize the library DB file and settings row."""
    db_path = session.db_path_for("library")
    asyncio.run(init_db_async(db_path, response_groups))
    click.echo(f"[init] DB ready at {db_path} with response_groups set.")


@library.command("full", help="Apply a full payload (initial load)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--response-token", default=None, help="Response state token (epoch ms) from HTTP header 'State-Token'")
@click.option("--note", default=None)
@pass_session
def cmd_full(session: Session, payload: Path, response_token: Optional[str], note: Optional[str]) -> None:
    """Apply a full export payload into the DB (initial load)."""
    db_path = session.db_path_for("library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    n = asyncio.run(
        full_import_async(
            db_path,
            data,
            response_token=response_token,
            note=note,
        )
    )
    click.echo(f"[full] Upserted {n} items → {db_path}.")


@library.command("delta", help="Apply a delta payload (incremental)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--request-token", default=None, help="State token sent in the GET (epoch ms)")
@click.option("--response-token", default=None, help="State token received in the response (epoch ms)")
@click.option("--note", default=None)
@pass_session
def cmd_delta(
    session: Session,
    payload: Path,
    request_token: Optional[str],
    response_token: Optional[str],
    note: Optional[str],
) -> None:
    """Apply an incremental delta payload into the DB."""
    db_path = session.db_path_for("library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    up, deleted = asyncio.run(
        delta_import_async(
            db_path,
            data,
            request_token=request_token,
            response_token=response_token,
            note=note,
        )
    )
    click.echo(f"[delta] Upserted {up}, soft-deleted {deleted} → {db_path}.")


@library.command("search", help="Search by title, subtitle or full_title")
@click.argument("needle", type=str)
@click.option("--limit", type=int, default=20, show_default=True)
@pass_session
def cmd_search(session: Session, needle: str, limit: int) -> None:
    """Case-insensitive LIKE search across title/subtitle/full_title."""
    db_path = session.db_path_for("library")
    rows = asyncio.run(query_search_async(db_path, needle, limit))
    if not rows:
        click.echo("No matches found.")
        return
    for asin, full_title in rows:
        click.echo(f"{asin} | {full_title}")


@library.command("search-fts", help="FTS5 search by full_title/title/subtitle")
@click.argument("query", type=str)
@click.option("--limit", type=int, default=20, show_default=True)
@pass_session
def cmd_search_fts(session: Session, query: str, limit: int) -> None:
    """FTS5 MATCH search across indexed columns."""
    db_path = session.db_path_for("library")
    rows = asyncio.run(query_search_fts_async(db_path, query, limit))
    if not rows:
        click.echo("No matches found.")
        return
    for asin, full_title in rows:
        click.echo(f"{asin} | {full_title}")


@library.command("fts-rebuild", help="Rebuild FTS index from content table (maintenance)")
@pass_session
def cmd_fts_rebuild(session: Session) -> None:
    """Run an FTS index rebuild."""
    db_path = session.db_path_for("library")
    asyncio.run(rebuild_fts_async(db_path))
    click.echo(f"[fts] Rebuilt items_fts → {db_path}.")


@library.command("query-plan", help="Show the SQLite query plan for a given SQL")
@click.argument("sql", type=str)
@click.argument("params", nargs=-1)
@pass_session
def cmd_query_plan(session: Session, sql: str, params: Tuple[str, ...]) -> None:
    """Show EXPLAIN QUERY PLAN for an arbitrary SQL statement."""
    db_path = session.db_path_for("library")
    rows = asyncio.run(explain_query_async(db_path, sql, tuple(params)))
    if not rows:
        click.echo("No plan output.")
        return
    click.echo("QUERY PLAN:")
    for r in rows:
        click.echo(f"- {r}")


@library.command(
    "inspect",
    help="Print stored JSON docs for given ASINs and/or title needles.",
)
@click.option("--asin", "asins", multiple=True, help="ASIN to fetch (can be given multiple times).")
@click.option("--title", "titles", multiple=True, help="Title/full_title/subtitle needle (can be given multiple times).")
@click.option("--fts/--no-fts", default=False, show_default=True, help="Use FTS MATCH for title needles (default: LIKE).")
@click.option("--limit-per", type=int, default=5, show_default=True, help="Max results per title needle.")
@click.option("--all/--active-only", "include_deleted", default=False, show_default=True, help="Include soft-deleted items too.")
@click.option("--pretty/--compact", default=True, show_default=True, help="Pretty-print JSON output.")
@click.option(
    "-J",
    "--json-out",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Write results as a single JSON object to the given file path instead of printing.",
)
@pass_session
def cmd_inspect(
    session: Session,
    asins: Tuple[str, ...],
    titles: Tuple[str, ...],
    fts: bool,
    limit_per: int,
    include_deleted: bool,
    pretty: bool,
    json_out: Path | None,
) -> None:
    """Inspect raw stored JSON documents by ASINs or title needles.

    Behavior:
        - If --json-out is provided, no CLI output is produced. Results are written
          as a single JSON object mapping {ASIN: obj} to the specified file.
        - If --json-out is NOT provided, results are printed to the console. Pretty
          formatting is controlled by --pretty/--compact.

    Args:
        session: Active CLI session providing DB access.
        asins: One or more ASINs to fetch.
        titles: One or more title/full_title/subtitle needles.
        fts: Whether to use FTS MATCH for title needles.
        limit_per: Maximum results per title needle.
        include_deleted: Include soft-deleted items when True.
        pretty: Pretty-print JSON when True (indentation).
        json_out: Destination file path for JSON output; disables CLI printing when set.
    """
    db_path = session.db_path_for("library")
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    # Collect by ASINs
    if asins:
        rows = asyncio.run(get_docs_by_asins(db_path, list(asins), include_deleted=include_deleted))
        for asin, full_title, doc in rows:
            if asin in seen:
                continue
            seen.add(asin)
            results.append((asin, full_title, doc))

    # Collect by title needles
    if titles:
        rows = asyncio.run(
            get_docs_by_titles(
                db_path,
                list(titles),
                use_fts=fts,
                limit_per=limit_per,
                include_deleted=include_deleted,
            )
        )
        for asin, full_title, doc in rows:
            if asin in seen:
                continue
            seen.add(asin)
            results.append((asin, full_title, doc))

    # If JSON file output is requested, write and return without any CLI noise.
    if json_out is not None:
        # Build mapping {asin: obj}, where obj is parsed JSON if possible, else raw string.
        out_map: dict[str, Any] = {}
        for idx, (_, _, doc) in enumerate(results, start=1):
            try:
                parsed = json.loads(doc)
            except Exception:
                parsed = doc
            out_map[idx] = parsed

        # Ensure parent directory exists.
        json_out.parent.mkdir(parents=True, exist_ok=True)

        with json_out.open("w", encoding="utf-8") as fh:
            json.dump(out_map, fh, indent=2 if pretty else None, ensure_ascii=False)
        return

    # CLI printing mode
    if not results:
        click.echo("No matching items.")
        return

    for idx, (asin, full_title, doc) in enumerate(results, start=1):
        try:
            obj: Any = json.loads(doc)
        except Exception:
            obj = doc

        click.echo(f"=== [{idx}] {asin} | {full_title}")
        if isinstance(obj, str):
            click.echo(obj)
        else:
            click.echo(json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False))


@library.command("export", help="Export library back to JSON (like library.json)")
@click.option(
    "--out",
    type=click.Path(path_type=Path, writable=True, dir_okay=False),
    default=Path("library.json"),
    show_default=True,
)
@click.option("--all/--active-only", "include_deleted", default=False, show_default=True)
@click.option("--pretty/--compact", default=True, show_default=True)
@click.option("--indent", type=int, default=4, show_default=True)
@click.option("--no-groups", is_flag=True, default=False, help="Omit response_groups from export.")
@click.option("--no-token", is_flag=True, default=False, help="Omit state_token from export.")
@pass_session
def cmd_export(
    session: Session,
    out: Path,
    include_deleted: bool,
    pretty: bool,
    indent: int,
    no_groups: bool,
    no_token: bool,
) -> None:
    """Export the current library into a JSON file compatible with restore."""
    db_path = session.db_path_for("library")
    data = asyncio.run(
        export_library_async(
            db_path,
            include_deleted=include_deleted,
            include_groups=not no_groups,
            include_state_token=not no_token,
        )
    )
    out.write_text(json.dumps(data, indent=indent if pretty else None, ensure_ascii=False), encoding="utf-8")
    click.echo(f"[export] Wrote {len(data.get('items', []))} items to {out}")


@library.command("remove", help="Remove the library database file")
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_session
def cmd_remove(session: Session, force: bool) -> None:
    """Delete the library DB file and its lock/journal sidecars."""
    db_path = session.db_path_for("library")
    lock_path = db_path.with_suffix(".lock")
    if not db_path.exists():
        click.echo(f"[remove] No database found at {db_path}")
        return
    if not force:
        click.confirm(f"Are you sure you want to delete the database at {db_path}?", abort=True)
    try:
        db_path.unlink()
        if lock_path.exists():
            lock_path.unlink()
        click.echo(f"[remove] Deleted database at {db_path}")
    except Exception as e:
        raise click.ClickException(f"Failed to delete {db_path}: {e}") from e


@library.command("restore", help="Restore library from an exported JSON file")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--replace/--merge", default=False, show_default=True)
@click.option("--fresh", is_flag=True, default=False, help="Delete existing DB before restoring.")
@click.option("--state-token", default=None, help="Override state token to persist in settings (raw value, e.g. epoch-ms).")
@pass_session
def cmd_restore(session: Session, payload: Path, replace: bool, fresh: bool, state_token: Optional[str]) -> None:
    """Restore from a previously exported JSON snapshot."""
    from audible_cli.db.async_db_library import restore_from_export_async  # local import to avoid cycles

    db_path = session.db_path_for("library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    if "items" not in data:
        raise click.ClickException("Input file must contain an 'items' array.")
    if "response_groups" not in data:
        raise click.ClickException("Input file must contain 'response_groups'.")
    if fresh:
        sidecars = [
            db_path,
            db_path.with_suffix(".lock"),
            db_path.with_suffix(".sqlite-wal") if db_path.suffix == ".sqlite" else db_path.with_name(db_path.name + "-wal"),
            db_path.with_suffix(".sqlite-shm") if db_path.suffix == ".sqlite" else db_path.with_name(db_path.name + "-shm"),
            db_path.with_name(db_path.name + "-journal"),
        ]
        removed_any = False
        for p in sidecars:
            try:
                if p and p.exists():
                    p.unlink()
                    removed_any = True
            except Exception as e:
                raise click.ClickException(f"Failed to remove '{p}': {e}")
        if removed_any:
            click.echo(f"[restore] Removed existing DB files for a fresh restore at {db_path}")
    up, deleted = asyncio.run(
        restore_from_export_async(
            db_path,
            data,
            replace=replace,
            note=f"restore:{payload.name}",
            state_token=state_token,
        )
    )
    if replace:
        click.echo(f"[restore] Upserted {up}, soft-deleted (by replace) {deleted} → {db_path}")
    else:
        click.echo(f"[restore] Upserted {up} → {db_path} (merge mode)")


@library.command("count", help="Show number of items in the library database")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output counts as JSON.")
@pass_session
def cmd_count(session: Session, as_json: bool) -> None:
    """Count active and soft-deleted items."""
    db_path = session.db_path_for("library")

    async def _count():
        async with open_db(db_path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM items WHERE is_deleted=0")
            active = (await cur.fetchone())[0]
            await cur.close()
            cur = await conn.execute("SELECT COUNT(*) FROM items WHERE is_deleted=1")
            deleted = (await cur.fetchone())[0]
            await cur.close()
            return active, deleted

    active, deleted = asyncio.run(_count())
    total = active + deleted
    if as_json:
        payload = {"active": active, "soft_deleted": deleted, "total": total}
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(f"[count] {active} active items, {deleted} soft-deleted items (total {total})")


@library.command("list-deleted")
@click.option("--limit", type=int, default=50, show_default=True, help="Max rows to display")
@click.option("--offset", type=int, default=0, show_default=True, help="Offset for paging")
@click.option("--json/--no-json", "as_json", default=False, show_default=True, help="Emit JSON instead of line output")
@click.option("--pretty/--no-pretty", default=True, show_default=True, help="Pretty-print JSON (only with --json)")
@pass_session
def list_deleted_cmd(session: Session, limit: int, offset: int, as_json: bool, pretty: bool) -> None:
    """Show soft-deleted items (line output by default, JSON with --json)."""
    db_path = session.db_path_for("library")
    rows, total = asyncio.run(list_soft_deleted_async(db_path, limit=limit, offset=offset))

    if as_json:
        payload = {
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "total": total,
            "items": [
                {
                    "asin": r["asin"],
                    "title": r["title"],
                    "subtitle": r.get("subtitle"),
                    "full_title": r["full_title"],
                    "deleted_utc": r.get("deleted_utc"),
                    "updated_utc": r.get("updated_utc"),
                }
                for r in rows
            ],
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return

    if not rows:
        click.echo("(no soft-deleted items)")
        return

    for r in rows:
        click.echo(f"{r['asin']}\t{r['full_title']}")
    shown = len(rows)
    click.echo(f"-- showing {shown} of {total} soft-deleted --", err=True)


@library.command("logs", help="Show recent sync logs (from sync_log table)")
@click.option("--limit", type=int, default=50, show_default=True, help="Max rows to display")
@click.option("--offset", type=int, default=0, show_default=True, help="Offset for paging")
@click.option("--order", type=click.Choice(["asc", "desc"], case_sensitive=False), default="desc", show_default=True, help="Sort by id ascending/descending")
@click.option("--json/--no-json", "as_json", default=False, show_default=True, help="Emit JSON instead of line output")
@click.option("--pretty/--no-pretty", default=True, show_default=True, help="Pretty-print JSON (only with --json)")
@click.option("--include-asins/--no-include-asins", default=False, show_default=True, help="Include full ASIN lists in line output")
@pass_session
def cmd_logs(session: Session, limit: int, offset: int, order: str, as_json: bool, pretty: bool, include_asins: bool) -> None:
    """Display rows from sync_log with optional JSON output."""
    db_path = session.db_path_for("library")
    rows, total = asyncio.run(list_sync_logs_async(db_path, limit=limit, offset=offset, order=order))

    if as_json:
        payload = {
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "total": total,
            "order": order,
            "items": rows,
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return

    if not rows:
        click.echo("(no logs)")
        return

    for r in rows:
        rid = r.get("id")
        req = r.get("request_time_utc") or "-"
        rsp = r.get("response_time_utc") or "-"
        http = r.get("http_status")
        up = r.get("num_upserted")
        de = r.get("num_soft_deleted")
        note = r.get("note") or ""

        line = f"{rid:>5}  {req} → {rsp}  http={http}  up={up} del={de}"
        if note:
            note_short = note if len(note) <= 120 else (note[:117] + "...")
            line += f'  note="{note_short}"'

        if include_asins:
            ups = r.get("upserted_asins") or []
            dels = r.get("soft_deleted_asins") or []
            if ups:
                line += f"  upserted[{len(ups)}]={','.join(ups[:8])}"
                if len(ups) > 8:
                    line += "…"
            if dels:
                line += f"  deleted[{len(dels)}]={','.join(dels[:8])}"
                if len(dels) > 8:
                    line += "…"

        click.echo(line)

    shown = len(rows)
    click.echo(f"-- showing {shown} of {total} logs (order={order}) --", err=True)


@library.command("sync", help="Sync library from Audible API using state token")
@pass_session
@click.option("--init/--no-init", default=False, show_default=True, help="Initialize new DB with provided --response-groups.")
@click.option(
    "--response-groups",
    default=None,
    help="Response groups for initial setup (CSV). Used only with --init; otherwise ignored.",
)
@page_size_option
@click.option("--image-sizes", default="900,1215,252,558,408,500", show_default=True, help="Image sizes for API.")
@click.option("--include-pending/--no-include-pending", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="Fetch but do not write to DB (debug).")
def cmd_sync(
    session: Session,
    init: bool,
    response_groups: Optional[str],
    image_sizes: str,
    include_pending: bool,
    dry_run: bool,
) -> None:
    """Synchronize the library with the Audible API in full/delta mode."""
    db_path = session.db_path_for("library")
    db_exists = db_path.exists()

    if init:
        if not response_groups:
            response_groups = DEFAULT_RESPONSE_GROUPS
        if db_exists:
            raise click.ClickException(f"Database already exists at {db_path}. Aborting --init.")
    else:
        if response_groups:
            raise click.ClickException("--response-groups is only allowed with --init.")
        if not db_exists:
            raise click.ClickException(f"Database does not exist at {db_path}. Run with --init to create a full snapshot.")

    if init:
        asyncio.run(ensure_initialized_async(db_path, response_groups=response_groups))
        settings = asyncio.run(get_settings_async(db_path))
    else:
        settings = asyncio.run(get_settings_async(db_path))
        if settings is None:
            raise click.ClickException("Database is not initialized. Run with --init and provide --response-groups.")

    if not response_groups:
        response_groups = settings.get("response_groups") or ""
    if response_groups.strip().startswith("["):
        try:
            arr = json.loads(response_groups)
            response_groups = ",".join([str(x).strip() for x in arr if str(x).strip()])
        except Exception:
            pass

    last_token = settings.get("last_state_token_raw")
    if not init and not last_token:
        raise click.ClickException("No state token stored. Run with --init to create a full snapshot.")

    num_results = session.params["page_size"]

    async def _run_sync() -> None:
        mode = "full" if init else "delta"
        newest_state_token: Optional[str] = None
        total_upserted = 0
        total_deleted = 0
        page_idx = 0

        if dry_run:
            pages = 0
            items_total = 0
            async for body, _st in iter_library_pages(
                session=session,
                init=init,
                response_groups=response_groups,
                num_results=num_results,
                image_sizes=image_sizes,
                include_pending=include_pending,
                last_state_token=None if init else str(last_token),
            ):
                pages += 1
                items_total += len((body or {}).get("items", []))
            click.echo(f"[sync:dry] mode={mode} pages={pages} items_total={items_total} new_state=None")
            return

        lock_path = db_path.with_suffix(".lock")
        async with AsyncFileLock(lock_path):
            async with open_db(db_path) as conn:
                await ensure_library_schema(conn)
                cur = await conn.execute("SELECT 1 FROM settings WHERE id=1")
                if await cur.fetchone() is None:
                    await cur.close()
                    raise click.ClickException("Database is not initialized. Run with --init and provide --response-groups.")
                await cur.close()

                await conn.execute("BEGIN IMMEDIATE;")
                try:
                    async for body, st in iter_library_pages(
                        session=session,
                        init=init,
                        response_groups=response_groups,
                        num_results=num_results,
                        image_sizes=image_sizes,
                        include_pending=include_pending,
                        last_state_token=None if init else str(last_token),
                    ):
                        page_idx += 1
                        if st:
                            newest_state_token = st

                        if mode == "full":
                            up = await full_import_async(
                                db_path,
                                body,
                                response_token=newest_state_token,
                                note=f"sync-full-page-{page_idx}",
                                conn=conn,  # single TX
                            )
                            total_upserted += up
                        else:
                            up, deleted = await delta_import_async(
                                db_path,
                                body,
                                request_token=str(last_token) if last_token is not None else None,
                                response_token=newest_state_token,
                                note=f"sync-delta-{page_idx}",
                                conn=conn,  # single TX
                            )
                            total_upserted += up
                            total_deleted += deleted

                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

        if newest_state_token:
            click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted}, new state_token={newest_state_token}")
        else:
            click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted} (no state token)")

    try:
        asyncio.run(_run_sync())
    except Exception as e:
        raise click.ClickException(f"sync failed: {e}") from e


# ---------------- fetch_library_api ----------------

RETRY_STATUS = {429, 500, 502, 503, 504}


def _local_time_header() -> str:
    """Return local time header value (ISO-8601 with seconds)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def iter_library_pages(
    *,
    session: Session,
    init: bool,
    response_groups: str,
    num_results: int,
    image_sizes: str,
    include_pending: bool,
    last_state_token: Optional[str],
) -> AsyncIterator[tuple[dict, Optional[str]]]:
    """Yield pages one by one as (page_body, state_token)."""
    import asyncio as _asyncio

    auth = session.auth
    tld = getattr(getattr(auth, "locale", None), "domain", None)
    if not tld:
        raise RuntimeError("auth.locale.domain missing – cannot determine marketplace.")
    base_url = f"https://api.audible.{tld}/1.0/library"

    mode = "full" if init else "delta"
    request_token = None if init else last_state_token
    if mode == "delta" and not request_token:
        raise ValueError("Delta sync requested (init=False) but last_state_token is missing.")

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json",
        "local-time": _local_time_header(),
    }
    ua = getattr(session, "user_agent", None)
    if ua:
        headers["User-Agent"] = ua

    base_params = {
        "image_sizes": image_sizes,
        "include_pending": "true" if include_pending else "false",
        "num_results": str(int(num_results)),
        "response_groups": response_groups,
        "status": "Active" if init else "Active,Revoked",
    }
    if not init:
        base_params["state_token"] = request_token

    async def _request_with_retry(
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict,
        headers: dict,
        max_retries: int = 6,
        base_backoff: float = 0.5,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await client.get(url, params=params, headers=headers)
            except Exception:
                if attempt <= max_retries:
                    await _asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
                    continue
                raise
            if resp.status_code in RETRY_STATUS and attempt <= max_retries:
                ra = resp.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else base_backoff * (2 ** (attempt - 1))
                except Exception:
                    delay = base_backoff * (2 ** (attempt - 1))
                await _asyncio.sleep(delay)
                continue
            return resp

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=60.0)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)

    async with make_async_client(session, timeout, limits) as client:
        continuation: Optional[str] = None
        while True:
            params = dict(base_params)
            if continuation:
                params["continuation_token"] = continuation

            resp = await _request_with_retry(client, base_url, params=params, headers=headers)
            if resp.status_code != 200:
                snippet = (resp.text or "")[:400]
                raise RuntimeError(f"HTTP {resp.status_code} fetching /1.0/library: {snippet}")

            st = resp.headers.get("State-Token")
            st = st if (st and st != "0") else None

            body = resp.json()
            yield body, st

            continuation = resp.headers.get("Continuation-Token")
            if not continuation:
                break
 
