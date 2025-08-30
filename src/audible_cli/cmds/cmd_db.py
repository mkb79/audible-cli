from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Optional

import click
from audible import Authenticator
from audible_cli.decorators import pass_session

from audible_cli.config import Session
from audible_cli.db.async_db_library import (
    ensure_initialized_async,
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


def db_path_for_session(session: Session, db_name: str) -> Path:
    """
    Build a stable, safe DB filename under session.app_dir using a hash of user_id + locale_code.
    session.auth is already a Python dict.
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
@click.option("--statuses", default=None, help="e.g. 'Active,Revoked'")
@pass_session
def cmd_init(session, response_groups: str, statuses: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
    asyncio.run(init_db_async(db_path, response_groups, statuses))
    click.echo(f"[init] DB ready at {db_path} with response_groups set.")


@library_cmd.command("full", help="Apply a full payload (initial load)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--response-token", default=None, help="Response state token (epoch ms) from HTTP header 'State-Token'")
@click.option("--statuses", default=None)
@click.option("--note", default=None)
@pass_session
def cmd_full(session, payload: Path, response_token, statuses: Optional[str], note: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    n = asyncio.run(
        full_import_async(
            db_path,
            data,
            response_token=response_token,
            statuses=statuses,
            note=note,
        )
    )
    click.echo(f"[full] Upserted {n} items → {db_path}.")


@library_cmd.command("delta", help="Apply a delta payload (incremental)")
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--request-token", default=None, help="State token sent in the GET (epoch ms)")
@click.option("--response-token", default=None, help="State token received in the response (epoch ms)")
@click.option("--statuses", default=None)
@click.option("--note", default=None)
@pass_session
def cmd_delta(session, payload: Path, request_token, response_token, statuses: Optional[str], note: Optional[str]) -> None:
    db_path = db_path_for_session(session, "library")
    data = json.loads(payload.read_text(encoding="utf-8"))
    up, deleted = asyncio.run(
        delta_import_async(
            db_path,
            data,
            request_token=request_token,
            response_token=response_token,
            statuses=statuses,
            note=note,
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
@click.option(
    "--asin",
    "asins",
    multiple=True,
    help="ASIN to fetch (can be given multiple times).",
)
@click.option(
    "--title",
    "titles",
    multiple=True,
    help="Title/full_title/subtitle needle (can be given multiple times).",
)
@click.option(
    "--fts/--no-fts",
    default=False,
    help="Use FTS MATCH for title needles (default: LIKE).",
    show_default=True,
)
@click.option(
    "--limit-per",
    type=int,
    default=5,
    show_default=True,
    help="Max results per title needle.",
)
@click.option(
    "--all/--active-only",
    "include_deleted",
    default=False,
    show_default=True,
    help="Include soft-deleted items too.",
)
@click.option(
    "--pretty/--compact",
    default=True,
    show_default=True,
    help="Pretty-print JSON output.",
)
@pass_session
def cmd_inspect(
    session,
    asins: tuple[str, ...],
    titles: tuple[str, ...],
    fts: bool,
    limit_per: int,
    include_deleted: bool,
    pretty: bool,
) -> None:
    """
    Examples:
      audible-cli db library inspect --asin B004V0CK0I --asin B00ABC1234
      audible-cli db library inspect --title "dune" --title "foundation"
      audible-cli db library inspect --title "dune*" --fts --limit-per 10
    """
    db_path = db_path_for_session(session, "library")

    # Collect matches
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    # Exact ASIN matches
    if asins:
        rows = asyncio.run(get_docs_by_asins(db_path, list(asins), include_deleted=include_deleted))
        for asin, full_title, doc in rows:
            if asin in seen:
                continue
            seen.add(asin)
            results.append((asin, full_title, doc))

    # Title-based matches
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

    if not results:
        click.echo("No matching items.")
        return

    # Print JSON (one block per item)
    for idx, (asin, full_title, doc) in enumerate(results, start=1):
        try:
            obj = json.loads(doc)
        except Exception:
            obj = doc  # if somehow non-JSON string slipped in

        click.echo(f"=== [{idx}] {asin} | {full_title}")
        if isinstance(obj, str):
            click.echo(obj)
        else:
            click.echo(json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False))


@library_cmd.command("export", help="Export library back to JSON (like library.json)")
@click.option(
    "--out",
    type=click.Path(path_type=Path, writable=True, dir_okay=False),
    default=Path("library.json"),
    show_default=True,
    help="Output JSON file path",
)
@click.option(
    "--all/--active-only",
    "include_deleted",
    default=False,
    show_default=True,
    help="Include soft-deleted items too.",
)
@click.option(
    "--pretty/--compact",
    default=True,
    show_default=True,
    help="Pretty-print JSON output.",
)
@click.option(
    "--indent",
    type=int,
    default=4,
    show_default=True,
    help="Number of spaces for JSON indentation (only if --pretty).",
)
@click.option(
    "--no-groups",
    is_flag=True,
    default=False,
    help="Omit response_groups from export.",
)
@click.option(
    "--no-token",
    is_flag=True,
    default=False,
    help="Omit state_token from export.",
)
@pass_session
def cmd_export(
    session,
    out: Path,
    include_deleted: bool,
    pretty: bool,
    indent: int,
    no_groups: bool,
    no_token: bool,
) -> None:
    db_path = db_path_for_session(session, "library")
    data = asyncio.run(
        export_library_async(
            db_path,
            include_deleted=include_deleted,
            include_groups=not no_groups,
            include_state_token=not no_token,
        )
    )
    out.write_text(
        json.dumps(
            data,
            indent=indent if pretty else None,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    click.echo(f"[export] Wrote {len(data.get('items', []))} items to {out}")


@library_cmd.command("remove", help="Remove the library database file")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Do not ask for confirmation.",
)
@pass_session
def cmd_remove(session, force: bool) -> None:
    db_path = db_path_for_session(session, "library")
    lock_path = db_path.with_suffix(".lock")

    if not db_path.exists():
        click.echo(f"[remove] No database found at {db_path}")
        return

    if not force:
        click.confirm(
            f"Are you sure you want to delete the database at {db_path}?",
            abort=True,
        )

    try:
        db_path.unlink()
        if lock_path.exists():
            lock_path.unlink()
        click.echo(f"[remove] Deleted database at {db_path}")
    except Exception as e:
        click.echo(f"[remove] Failed to delete {db_path}: {e}")


@library_cmd.command("restore", help="Restore library from an exported JSON file")
@click.option(
    "--payload",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="Exported JSON file (must contain 'items' and 'response_groups')",
)
@click.option(
    "--replace/--merge",
    default=False,
    show_default=True,
    help="If set, soft-delete all items not present in the export (snapshot restore).",
)
@click.option(
    "--statuses",
    default="Active,Revoked",
    show_default=True,
    help="Statuses string to persist in settings (default: 'Active,Revoked').",
)
@click.option(
    "--fresh",
    is_flag=True,
    default=False,
    help="Delete existing DB before restore (also removes lock/WAL/SHM files).",
)
@click.option(
    "--state-token",
    default=None,
    help="Override state token to persist in settings (raw value, e.g. epoch-ms). "
         "If not provided, uses 'state_token' from the file when available.",
)
@pass_session
def cmd_restore(session, payload: Path, replace: bool, statuses: str, fresh: bool, state_token: Optional[str]) -> None:
    """
    Restore from an exported library JSON (created via `db library export`).

    - Default (merge): Items from the file are upserted, existing DB items remain.
    - With --replace: Items not in the file are soft-deleted (snapshot restore).
    - With --fresh: Remove any existing DB files before restoring.
    - With --state-token: Persist this token; otherwise use token from file if present.
    """
    db_path = db_path_for_session(session, "library")
    data = json.loads(payload.read_text(encoding="utf-8"))

    if "items" not in data:
        raise click.ClickException("Input file must contain an 'items' array.")
    if "response_groups" not in data:
        raise click.ClickException("Input file must contain 'response_groups'.")

    # --fresh: remove existing DB + sidecars
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
            statuses=statuses,
            note=f"restore:{payload.name}",
            state_token=state_token,  # may be None -> then payload['state_token'] is used if present
        )
    )

    if replace:
        click.echo(f"[restore] Upserted {up}, soft-deleted (by replace) {deleted} → {db_path}")
    else:
        click.echo(f"[restore] Upserted {up} → {db_path} (merge mode)")


@library_cmd.command("count", help="Show number of items in the library database")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output counts as JSON object (machine-readable).",
)
@pass_session
def cmd_count(session, as_json: bool) -> None:
    """Print how many items are active vs. soft-deleted (optionally as JSON)."""
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


@library_cmd.command("sync", help="Sync library from Audible API using state token")
@pass_session
@click.option(
    "--init/--no-init",
    default=False,
    show_default=True,
    help="Initialize (create) a new DB with provided --response-groups. Aborts if DB already exists.",
)
@click.option(
    "--response-groups",
    default=None,
    help="Response groups for initial setup (CSV). Required with --init. Not allowed otherwise.",
)
@click.option(
    "--statuses",
    default="Active,Revoked",
    show_default=True,
    help="Statuses to request (CSV). Stored in settings.",
)
@click.option(
    "--num-results",
    type=int,
    default=200,
    show_default=True,
    help="Page size to request from the API.",
)
@click.option(
    "--image-sizes",
    default="900,1215,252,558,408,500",
    show_default=True,
    help="Image sizes parameter passed through to API.",
)
@click.option(
    "--include-pending/--no-include-pending",
    default=True,
    show_default=True,
    help="Whether to include pending items.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Fetch but do not write to DB (debug).",
)
def cmd_sync(
    session,
    init: bool,
    response_groups: str | None,
    statuses: str,
    num_results: int,
    image_sizes: str,
    include_pending: bool,
    dry_run: bool,
) -> None:
    """
    Behavior:
      - --init: create schema + settings (requires --response-groups). Abort if DB already exists.
                Then perform a FULL sync (no state token required).
      - --no-init:
          * DB must be initialized AND have a stored state token -> DELTA sync.
          * If no state token -> abort with hint to use --init.
    """
    db_path = db_path_for_session(session, "library")
    db_exists = db_path.exists()

    # Enforce response_groups usage policy
    if init:
        if not response_groups:
            raise click.ClickException("--response-groups is required when using --init.")
        if db_exists:
            raise click.ClickException(f"Database already exists at {db_path}. Aborting --init.")
    else:
        if response_groups:
            raise click.ClickException("--response-groups is only allowed with --init.")

    # Initialize when requested, or load settings otherwise
    if init:
        asyncio.run(ensure_initialized_async(db_path, response_groups=response_groups, statuses=statuses))
        settings = asyncio.run(get_settings_async(db_path))
    else:
        settings = asyncio.run(get_settings_async(db_path))
        if settings is None:
            raise click.ClickException(
                "Database is not initialized. Run with --init and provide --response-groups."
            )

    # Determine response_groups to send
    if not response_groups:
        response_groups = settings.get("response_groups") or ""
    if response_groups.strip().startswith("["):
        try:
            arr = json.loads(response_groups)
            response_groups = ",".join([str(x).strip() for x in arr if str(x).strip()])
        except Exception:
            pass

    # Delta requires last token present
    last_token = settings.get("last_state_token_raw")
    if not init and not last_token:
        raise click.ClickException(
            "No state token stored for this database. Please re-sync with --init and provide --response-groups."
        )

    # ---- Only one call: you implement fetch_library_api() ----
    # Contract suggestion (you can change it in your own implementation):
    # fetch_library_api(...) -> tuple[str mode, list[dict] pages, Optional[str] response_token, Optional[str] request_token]
    #   mode in {"full","delta"}
    #   pages: list of payload dicts shaped like Audible's /1.0/library response(s)
    #   response_token: the newest State-Token to persist (header value)
    #   request_token:  the state token that was sent (for delta_import bookkeeping)
    try:
        mode, pages, response_token, request_token = asyncio.run(fetch_library_api(
            session=session,
            init=init,
            response_groups=response_groups,
            statuses=statuses,
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
        # Apply full pages (each page is a 'full' payload)
        for idx, body in enumerate(pages or [], start=1):
            up = asyncio.run(
                full_import_async(
                    db_path,
                    body,
                    response_token=response_token,   # persist latest known token
                    statuses=statuses,
                    note=f"sync-full-page-{idx}",
                )
            )
            total_upserted += up

    elif mode == "delta":
        # Apply delta pages (usually 1)
        for idx, body in enumerate(pages or [], start=1):
            up, deleted = asyncio.run(
                delta_import_async(
                    db_path,
                    body,
                    request_token=str(request_token) if request_token is not None else None,
                    response_token=response_token,
                    statuses=statuses,
                    note=f"sync-delta-{idx}",
                )
            )
            total_upserted += up
            total_deleted += deleted
    else:
        raise click.ClickException(f"Unsupported sync mode returned by fetch_library_api: {mode!r}")

    # Finish output
    if response_token:
        click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted}, new state_token={response_token}")
    else:
        click.echo(f"[sync] mode={mode} Upserted={total_upserted}, Soft-deleted={total_deleted} (no state token)")


async def fetch_library_api(
    *,
    session,
    init: bool,
    response_groups: str,
    statuses: str,
    num_results: int,
    image_sizes: str,
    include_pending: bool,
    last_state_token: Optional[str],
    dry_run: bool,
) -> tuple[str, list[dict], Optional[str], Optional[str]]:
    """
    Return (mode, pages, response_token, request_token)
      - mode: 'full' or 'delta'
      - pages: list of response dicts (each like /1.0/library JSON)
      - response_token: newest State-Token from headers
      - request_token: token you used in the request (for delta bookkeeping)
    """
    raise NotImplementedError("Implement me")
