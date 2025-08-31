from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional

import click
import httpx
from audible import Authenticator
from audible_cli.decorators import pass_session

from audible_cli.config import Session
from audible_cli.db.async_db_library import (
    ensure_initialized_async,
    list_soft_deleted_async,
    get_settings_async,
    init_db_async,
    full_import_async,
    delta_import_async,
    query_search_async,
    query_search_fts_async,
    rebuild_fts_async,
    explain_query_async,
    get_docs_by_asins,
    get_docs_by_titles,
    export_library_async,
    restore_from_export_async,
    open_db,
)

try:
    import h2  # noqa: F401
    HAS_H2 = True
except ImportError:
    HAS_H2 = False
    click.echo("[init] WARNING: h2 module not found. Switching to HTTP/1.1.")


DEFAULT_RESPONSE_GROUPS = (
        "badge_types,is_archived,is_finished,is_playable,is_removable,is_visible,"
        "order_details,origin_asin,percent_complete,shared,ws4v_rights,badges,"
        "category_ladders,category_media,category_metadata,contributors,customer_rights,"
        "media,product_attrs,product_desc,product_details,product_extended_attrs,"
        "product_plans,product_plan_details,profile_sharing,rating,relationships_v2,"
        "sample,sku,pdf_url,series"
    )


def make_async_client(session, timeout, limits) -> httpx.AsyncClient:
    """
    Create an AsyncClient.
    - If the `h2` package is installed → enable http2.
    - Otherwise → fall back to HTTP/1.1.
    """
    return httpx.AsyncClient(
        auth=session.auth,
        timeout=timeout,
        limits=limits,
        http2=HAS_H2,
    )


def db_path_for_session(session: Session, db_name: str) -> Path:
    """
    Build a stable, safe DB filename under session.app_dir using a hash of user_id + locale_code.
    """
    auth: Authenticator = session.auth
    user_id = auth.customer_info.get("user_id")
    locale = auth.locale.country_code
    key = f"{user_id}#{locale}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    app_dir = Path(session.app_dir)
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / f"{db_name}_{digest}.sqlite"


@click.group("db", help="Manage local SQLite databases (library, wishlist, user, ...)")
def cli() -> None:
    """Root group for DB-related commands."""


@cli.group("library", help="Manage the user's library database")
def library_cmd() -> None:
    """Group of commands related to the library database."""


@library_cmd.command("init", help="Initialize DB for this session user with fixed response_groups")
@click.option("--response-groups", required=True, help="Response groups string used for ALL future requests")
@pass_session
def cmd_init(session, response_groups: str) -> None:
    db_path = db_path_for_session(session, "library")
    asyncio.run(init_db_async(db_path, response_groups))
    click.echo(f"[init] DB ready at {db_path} with response_groups set.")


@library_cmd.command("full", help="Apply a full payload (initial load)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--response-token", default=None, help="Response state token (epoch ms) from HTTP header 'State-Token'")
@click.option("--note", default=None)
@pass_session
def cmd_full(session, payload: Path, response_token, note: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    n = asyncio.run(
        full_import_async(
            db_path,
            data,
            response_token=response_token,
            note=note,
            request_statuses="Active",
        )
    )
    click.echo(f"[full] Upserted {n} items → {db_path}.")


@library_cmd.command("delta", help="Apply a delta payload (incremental)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--request-token", default=None, help="State token sent in the GET (epoch ms)")
@click.option("--response-token", default=None, help="State token received in the response (epoch ms)")
@click.option("--note", default=None)
@pass_session
def cmd_delta(session, payload: Path, request_token, response_token, note: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    up, deleted = asyncio.run(
        delta_import_async(
            db_path,
            data,
            request_token=request_token,
            response_token=response_token,
            note=note,
            request_statuses="Active,Revoked",
        )
    )
    click.echo(f"[delta] Upserted {up}, soft-deleted {deleted} → {db_path}.")


@library_cmd.command("search", help="Search by title, subtitle or full_title")
@click.argument("needle", type=str)
@click.option("--limit", type=int, default=20, show_default=True)
@pass_session
def cmd_search(session, needle: str, limit: int) -> None:
    db_path = db_path_for_session(session, "library")
    rows = asyncio.run(query_search_async(db_path, needle, limit))
    if not rows:
        click.echo("No matches found.")
        return
    for asin, full_title in rows:
        click.echo(f"{asin} | {full_title}")


@library_cmd.command("search-fts", help="FTS5 search by full_title/title/subtitle")
@click.argument("query", type=str)
@click.option("--limit", type=int, default=20, show_default=True)
@pass_session
def cmd_search_fts(session, query: str, limit: int) -> None:
    db_path = db_path_for_session(session, "library")
    rows = asyncio.run(query_search_fts_async(db_path, query, limit))
    if not rows:
        click.echo("No matches found.")
        return
    for asin, full_title in rows:
        click.echo(f"{asin} | {full_title}")


@library_cmd.command("fts-rebuild", help="Rebuild FTS index from content table (maintenance)")
@pass_session
def cmd_fts_rebuild(session) -> None:
    db_path = db_path_for_session(session, "library")
    asyncio.run(rebuild_fts_async(db_path))
    click.echo(f"[fts] Rebuilt items_fts → {db_path}.")


@library_cmd.command("query-plan", help="Show the SQLite query plan for a given SQL")
@click.argument("sql", type=str)
@click.argument("params", nargs=-1)
@pass_session
def cmd_query_plan(session, sql: str, params: tuple[str]) -> None:
    db_path = db_path_for_session(session, "library")
    rows = asyncio.run(explain_query_async(db_path, sql, tuple(params)))
    if not rows:
        click.echo("No plan output.")
        return
    click.echo("QUERY PLAN:")
    for r in rows:
        click.echo(f"- {r}")


@library_cmd.command(
    "inspect",
    help="Print stored JSON docs for given ASINs and/or title needles.",
)
@click.option("--asin","asins", multiple=True, help="ASIN to fetch (can be given multiple times).")
@click.option("--title","titles", multiple=True, help="Title/full_title/subtitle needle (can be given multiple times).")
@click.option("--fts/--no-fts", default=False, show_default=True, help="Use FTS MATCH for title needles (default: LIKE).")
@click.option("--limit-per", type=int, default=5, show_default=True, help="Max results per title needle.")
@click.option("--all/--active-only","include_deleted", default=False, show_default=True, help="Include soft-deleted items too.")
@click.option("--pretty/--compact", default=True, show_default=True, help="Pretty-print JSON output.")
@pass_session
def cmd_inspect(session, asins: tuple[str, ...], titles: tuple[str, ...], fts: bool, limit_per: int, include_deleted: bool, pretty: bool) -> None:
    db_path = db_path_for_session(session, "library")
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    if asins:
        rows = asyncio.run(get_docs_by_asins(db_path, list(asins), include_deleted=include_deleted))
        for asin, full_title, doc in rows:
            if asin in seen:
                continue
            seen.add(asin)
            results.append((asin, full_title, doc))
    if titles:
        rows = asyncio.run(
            get_docs_by_titles(
                db_path, list(titles), use_fts=fts, limit_per=limit_per, include_deleted=include_deleted
            )
        )
        for asin, full_title, doc in rows:
            if asin in seen:
                continue
            seen.add(asin)
            results.append((asin, full_title, doc))
    if not results:
        click.echo("No matching items.")
        return
    for idx, (asin, full_title, doc) in enumerate(results, start=1):
        try:
            obj = json.loads(doc)
        except Exception:
            obj = doc
        click.echo(f"=== [{idx}] {asin} | {full_title}")
        if isinstance(obj, str):
            click.echo(obj)
        else:
            click.echo(json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False))


@library_cmd.command("export", help="Export library back to JSON (like library.json)")
@click.option("--out", type=click.Path(path_type=Path, writable=True, dir_okay=False), default=Path("library.json"), show_default=True)
@click.option("--all/--active-only","include_deleted", default=False, show_default=True)
@click.option("--pretty/--compact", default=True, show_default=True)
@click.option("--indent", type=int, default=4, show_default=True)
@click.option("--no-groups", is_flag=True, default=False, help="Omit response_groups from export.")
@click.option("--no-token", is_flag=True, default=False, help="Omit state_token from export.")
@pass_session
def cmd_export(session, out: Path, include_deleted: bool, pretty: bool, indent: int, no_groups: bool, no_token: bool) -> None:
    db_path = db_path_for_session(session, "library")
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


@library_cmd.command("remove", help="Remove the library database file")
@click.option("--force", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_session
def cmd_remove(session, force: bool) -> None:
    db_path = db_path_for_session(session, "library")
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
        click.echo(f"[remove] Failed to delete {db_path}: {e}")


@library_cmd.command("restore", help="Restore library from an exported JSON file")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--replace/--merge", default=False, show_default=True)
@click.option("--fresh", is_flag=True, default=False, help="Delete existing DB before restoring.")
@click.option("--state-token", default=None, help="Override state token to persist in settings (raw value, e.g. epoch-ms).")
@pass_session
def cmd_restore(session, payload: Path, replace: bool, fresh: bool, state_token: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
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


@library_cmd.command("count", help="Show number of items in the library database")
@click.option("--json","as_json", is_flag=True, default=False, help="Output counts as JSON.")
@pass_session
def cmd_count(session, as_json: bool) -> None:
    db_path = db_path_for_session(session, "library")
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


@library_cmd.command("list-deleted")
@click.option("--limit",  type=int, default=50, show_default=True, help="Max rows to display")
@click.option("--offset", type=int, default=0,  show_default=True, help="Offset for paging")
@click.option("--json/--no-json", "as_json", default=False, show_default=True,
              help="Emit JSON instead of line output")
@click.option("--pretty/--no-pretty", default=True, show_default=True,
              help="Pretty-print JSON (only with --json)")
@click.pass_obj
def list_deleted_cmd(session, limit: int, offset: int, as_json: bool, pretty: bool):
    """
    Show soft-deleted items. Defaults to line output:
      ASIN<TAB>full_title
    Use --json for a JSON payload (asin, title, subtitle, full_title, deleted_utc, updated_utc).
    """
    db_path = db_path_for_session(session, "library")

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
        print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return

    # Default: line output
    # Format: ASIN<TAB>full_title  (full_title is already "title[: subtitle]")
    # If you prefer title + " — " + subtitle manually, switch to that.
    if not rows:
        # Small friendly hint when DB exists but nothing is deleted
        click.echo("(no soft-deleted items)")
        return

    for r in rows:
        click.echo(f"{r['asin']}\t{r['full_title']}")
    # Optional footer for paging context:
    shown = len(rows)
    click.echo(f"-- showing {shown} of {total} soft-deleted --", err=True)


@library_cmd.command("sync", help="Sync library from Audible API using state token")
@pass_session
@click.option("--init/--no-init", default=False, show_default=True,
              help="Initialize (create) a new DB with provided --response-groups. Aborts if DB already exists.")
@click.option("--response-groups", default=None,
              help="Response groups for initial setup (CSV). Using with --init. If left, the default group is used. Not allowed otherwise.")
@click.option("--num-results", type=int, default=200, show_default=True, help="Page size to request from the API.")
@click.option("--image-sizes", default="900,1215,252,558,408,500", show_default=True, help="Image sizes for API.")
@click.option("--include-pending/--no-include-pending", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="Fetch but do not write to DB (debug).")
def cmd_sync(session, init: bool, response_groups: str | None, num_results: int, image_sizes: str, include_pending: bool, dry_run: bool) -> None:
    db_path = db_path_for_session(session, "library")
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

    # Select statuses per mode (not stored in settings)
    used_statuses = "Active" if init else "Active,Revoked"

    try:
        mode, pages, response_token, request_token = asyncio.run(fetch_library_api(
            session=session,
            init=init,
            response_groups=response_groups,
            num_results=num_results,
            image_sizes=image_sizes,
            include_pending=include_pending,
            last_state_token=None if init else str(last_token),
            dry_run=dry_run,
        ))
    except Exception as e:
        raise click.ClickException(f"fetch_library_api failed: {e}")

    if dry_run:
        total_items = sum(len((p or {}).get("items", [])) for p in (pages or []))
        click.echo(f"[sync:dry] mode={mode} pages={len(pages or [])} items_total={total_items} new_state={response_token}")
        return

    total_upserted = 0
    total_deleted = 0

    if mode == "full":
        for idx, body in enumerate(pages or [], start=1):
            up = asyncio.run(
                full_import_async(
                    db_path,
                    body,
                    response_token=response_token,
                    note=f"sync-full-page-{idx}",
                    request_statuses=used_statuses,
                )
            )
            total_upserted += up
    elif mode == "delta":
        for idx, body in enumerate(pages or [], start=1):
            up, deleted = asyncio.run(
                delta_import_async(
                    db_path,
                    body,
                    request_token=str(request_token) if request_token is not None else None,
                    response_token=response_token,
                    note=f"sync-delta-{idx}",
                    request_statuses=used_statuses,
                )
            )
            total_upserted += up
            total_deleted += deleted
    else:
        raise click.ClickException(f"Unsupported sync mode returned by fetch_library_api: {mode!r}")

    if response_token:
        click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted}, new state_token={response_token}")
    else:
        click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted} (no state token)")


# ---------------- fetch_library_api ----------------

async def fetch_library_api(
    *,
    session,
    init: bool,
    response_groups: str,
    num_results: int,
    image_sizes: str,
    include_pending: bool,
    last_state_token: Optional[str],
    dry_run: bool,
) -> tuple[str, list[dict], Optional[str], Optional[str]]:
    """
    Fetch the Audible library pages.

    Returns:
        (mode, pages, response_token, request_token)
    """
    import httpx
    import asyncio
    from datetime import datetime

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def _local_time_header() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

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
                    await asyncio.sleep(base_backoff * (2 ** (attempt - 1)))
                    continue
                raise
            if resp.status_code in RETRY_STATUS and attempt <= max_retries:
                ra = resp.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else base_backoff * (2 ** (attempt - 1))
                except Exception:
                    delay = base_backoff * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                continue
            return resp

    auth = session.auth
    tld = getattr(getattr(auth, "locale", None), "domain", None)
    if not tld:
        raise RuntimeError("auth.locale.domain missing – cannot determine marketplace.")
    base_url = f"https://api.audible.{tld}/1.0/library"

    if init:
        mode = "full"
        request_token: Optional[str] = None
        used_statuses = "Active"
    else:
        mode = "delta"
        request_token = last_state_token
        if not request_token:
            raise ValueError("Delta sync requested (init=False) but last_state_token is missing.")
        used_statuses = "Active,Revoked"

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
        "status": used_statuses,
    }
    if mode == "delta":
        base_params["state_token"] = request_token

    pages: list[dict] = []
    continuation: Optional[str] = None
    newest_state_token: Optional[str] = None

    if dry_run:
        return (mode, pages, None, request_token)

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=60.0)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)

    async with make_async_client(session, timeout, limits) as client:
        while True:
            params = dict(base_params)
            if continuation:
                params["continuation_token"] = continuation

            resp = await _request_with_retry(client, base_url, params=params, headers=headers)

            if resp.status_code != 200:
                snippet = ""
                try:
                    snippet = resp.text[:400]
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {resp.status_code} fetching /1.0/library: {snippet}")

            st = resp.headers.get("State-Token")
            if st and st != "0":
                newest_state_token = st

            continuation = resp.headers.get("Continuation-Token")

            body = resp.json()
            pages.append(body)

            if not continuation:
                break

    return (mode, pages, newest_state_token, request_token)
