from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import click

from audible_cli.config import Session
from audible_cli.decorators import pass_session
from audible_cli.db import open_db
from audible_cli.db.async_db_assets import (
    AssetKindName,
    DownloadStatus,

    ensure_assets_schema,
    ensure_asset_async,
    get_asset_matrix_for_asin_async,
    list_assets_async,
    list_missing_assets_async,
    log_download_async,
    upsert_asset_async,
)


@click.group("assets", help="Manage asset records (Audio, PDF, Cover, Annotations, Chapters).")
def assets() -> None:
    """Top-level Click group for asset-related DB commands."""


# -----------------------------------------------------------------------------
# upsert
# -----------------------------------------------------------------------------

@assets.command("upsert", help="Insert or update a single asset record.")
@click.option("--asin", required=True, help="Audible ASIN.")
@click.option(
    "--kind",
    required=True,
    type=click.Choice(["AUDIO", "COVER", "PDF", "CHAPTERS", "ANNOTATIONS"], case_sensitive=True),
    help="Logical asset kind.",
)
@click.option(
    "--variant",
    default=None,
    help="AUDIO: AAX/AAXC; COVER: resolution (e.g., 900); CHAPTERS: flat/tree; others: optional.",
)
@click.option(
    "--expected-path",
    type=click.Path(path_type=Path, dir_okay=False, resolve_path=True),
    default=None,
    help="Intended local storage path for the asset.",
)
@click.option(
    "--meta",
    "meta_json",
    type=str,
    default=None,
    help='Optional JSON metadata, e.g. \'{"mime":"image/jpeg"}\'.',
)
@pass_session
def cmd_upsert(
    session: Any,
    *,
    asin: str,
    kind: AssetKindName,
    variant: Optional[str],
    expected_path: Optional[Path],
    meta_json: Optional[str],
) -> None:
    """Create or update a single asset row using a managed connection."""
    db_path = session.db_path_for("library")
    meta: Optional[dict[str, Any]] = None
    if meta_json:
        try:
            meta = json.loads(meta_json)
        except Exception as exc:
            raise click.ClickException(f"--meta is not valid JSON: {exc}") from exc

    asset_id = asyncio.run(
        upsert_asset_async(
            db_path,
            asin=asin,
            kind=kind,
            variant=variant,
            expected_path=expected_path,
            meta=meta,
        )
    )
    click.echo(
        json.dumps(
            {"asset_id": asset_id, "asin": asin, "kind": kind, "variant": variant or ""},
            ensure_ascii=False,
        )
    )


# -----------------------------------------------------------------------------
# list
# -----------------------------------------------------------------------------

@assets.command("list", help="List assets (optionally filtered).")
@click.option("--asin", default=None, help="Filter by ASIN.")
@click.option(
    "--kind",
    type=click.Choice(["AUDIO", "COVER", "PDF", "CHAPTERS", "ANNOTATIONS"], case_sensitive=True),
    default=None,
    help="Filter by asset kind.",
)
@click.option("--variant", default=None, help="Filter by variant.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.option("--json/--no-json", "as_json", default=False, show_default=True)
@click.option("--pretty/--compact", default=True, show_default=True)
@pass_session
def cmd_list(
    session: Any,
    *,
    asin: Optional[str],
    kind: Optional[AssetKindName],
    variant: Optional[str],
    limit: int,
    offset: int,
    as_json: bool,
    pretty: bool,
) -> None:
    """List assets with their last download status if available."""
    db_path = session.db_path_for("library")
    rows = asyncio.run(
        list_assets_async(
            db_path,
            asin=asin,
            kind=kind,
            variant=variant,
            limit=limit,
            offset=offset,
        )
    )

    if as_json:
        payload = {"count": len(rows), "items": rows}
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return

    if not rows:
        click.echo("(no assets)")
        return

    for r in rows:
        asin_s = r.get("asin", "")
        kind_s = r.get("kind", "")
        var_s = r.get("variant", "") or ""
        stat = r.get("last_status") or "-"
        path = r.get("storage_path") or r.get("expected_path") or "-"
        size = r.get("bytes_written")
        size_s = str(size) if size is not None else "-"
        click.echo(f"{asin_s}\t{kind_s}\t{var_s}\t{stat}\t{size_s}\t{path}")


# -----------------------------------------------------------------------------
# matrix (status for one ASIN)
# -----------------------------------------------------------------------------

@assets.command("matrix", help="Show current asset matrix (kind/variant/status) for an ASIN.")
@click.option("--asin", required=True, help="Audible ASIN.")
@click.option("--json/--no-json", "as_json", default=False, show_default=True)
@click.option("--pretty/--compact", default=True, show_default=True)
@pass_session
def cmd_matrix(
    session: Any,
    *,
    asin: str,
    as_json: bool,
    pretty: bool,
) -> None:
    """Show latest status per asset for the given ASIN."""
    db_path = session.db_path_for("library")

    async def _work() -> list[dict[str, Any]]:
        async with open_db(db_path) as conn:
            await ensure_assets_schema(conn)
            return await get_asset_matrix_for_asin_async(conn, asin)

    rows = asyncio.run(_work())

    if as_json:
        click.echo(json.dumps({"asin": asin, "items": rows}, ensure_ascii=False, indent=2 if pretty else None))
        return

    if not rows:
        click.echo("(no assets)")
        return

    for r in rows:
        kind_s = r.get("kind", "")
        var_s = r.get("variant", "") or ""
        stat = r.get("status") or "-"
        path = r.get("storage_path") or "-"
        size = r.get("bytes_written")
        size_s = str(size) if size is not None else "-"
        click.echo(f"{kind_s}\t{var_s}\t{stat}\t{size_s}\t{path}")


# -----------------------------------------------------------------------------
# missing (assets without a successful download)
# -----------------------------------------------------------------------------

@assets.command("missing", help="List assets that have no successful download yet.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--json/--no-json", "as_json", default=False, show_default=True)
@click.option("--pretty/--compact", "pretty", default=True, show_default=True)
@click.option(
    "--all/--existing",
    "all_kinds",
    default=False,
    show_default=True,
    help=(
        "If set, consider the full cross-product of (ASIN x logical kinds/variants) "
        "and show any combination without a SUCCESS download. "
        "Does not insert rows."
    ),
)
@click.option(
    "--audio-variants",
    default="AAX,AAXC",
    show_default=True,
    help="Comma-separated audio variants to consider when using --all.",
)
@click.option(
    "--chapters-variants",
    default="flat,tree",
    show_default=True,
    help="Comma-separated chapters variants to consider when using --all.",
)
@click.option(
    "--cover-sizes",
    default="900,1215,252,558,408,500",
    show_default=True,
    help="Comma-separated cover sizes to consider when using --all.",
)
@pass_session
def cmd_assets_missing(
    session: Session,
    limit: int,
    as_json: bool,
    pretty: bool,
    all_kinds: bool,
    audio_variants: str,
    chapters_variants: str,
    cover_sizes: str,
) -> None:
    """
    Print asset combinations missing a successful download.

    Modes:
      * --existing (default): Only existing rows in `assets` are checked.
      * --all: Synthesize all logical combinations (AUDIO:AAX,AAXC;
               COVER:<sizes>; PDF:''; CHAPTERS:flat,tree; ANNOTATIONS:'')
               for every active item and report combinations without a SUCCESS download.
    """
    db_path = session.db_path_for("library")

    if not all_kinds:
        # Existing behavior: rely on asset rows already present.
        async def _go_existing() -> list[tuple[str, str, str]]:
            async with open_db(db_path) as conn:
                await ensure_assets_schema(conn)
                return await list_missing_assets_async(conn, limit=limit)

        rows = asyncio.run(_go_existing())
        if not rows:
            click.echo("(none missing)")
            return

        if as_json:
            payload = {
                "count": len(rows),
                "items": [{"asin": a, "kind": k, "variant": v} for a, k, v in rows],
            }
            click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
            return

        for asin, kind, variant in rows:
            click.echo(f"{asin}\t{kind}\t{variant}")
        click.echo(f"-- showing {len(rows)} missing (existing assets only) --", err=True)
        return

    # --all mode: build the full matrix in SQL without inserting into `assets`.
    cover_list = [s.strip() for s in (cover_sizes or "").split(",") if s.strip()]
    audio_list = [s.strip() for s in (audio_variants or "").split(",") if s.strip()]
    chapters_list = [s.strip() for s in (chapters_variants or "").split(",") if s.strip()]

    tuples: list[tuple[str, str]] = [
        ("PDF", ""),
        ("ANNOTATIONS", ""),
    ]

    for v in audio_list:
        tuples.append(("AUDIO", v))

    for v in chapters_list:
        tuples.append(("CHAPTERS", v))

    for c in cover_list:
        tuples.append(("COVER", c))

    # Build VALUES (?, ?) x N safely.
    values_clause = ",".join(["(?, ?)"] * len(tuples))
    sql = (
            "WITH kinds(kind, variant) AS (VALUES " + values_clause + ") "
            + "SELECT i.asin, k.kind, k.variant "
            + "FROM (SELECT asin FROM items WHERE is_deleted = 0) AS i "
            + "CROSS JOIN kinds AS k "
            + "JOIN asset_kind ak ON ak.name = k.kind "
            + "LEFT JOIN assets a "
            + "  ON a.asin = i.asin AND a.kind_id = ak.id AND a.variant = k.variant "
            + "LEFT JOIN v_asset_last_download d "
            + "  ON d.asset_id = a.id AND d.status = 'SUCCESS' "
            + "WHERE d.id IS NULL "
            + "ORDER BY i.asin, k.kind, k.variant "
            + "LIMIT ?"
    )

    # Flatten tuples into parameters and append limit.
    args: list[Any] = []
    for kind, variant in tuples:
        args.extend([kind, variant])
    args.append(limit)

    async def _go_all() -> list[tuple[str, str, str]]:
        async with open_db(db_path) as conn:
            # Ensure schema is present; `assets` has a FK to `items`.
            await ensure_assets_schema(conn)
            cur = await conn.execute(sql, tuple(args))
            rows2 = await cur.fetchall()
            await cur.close()
            return [(r[0], r[1], r[2]) for r in rows2]

    rows = asyncio.run(_go_all())
    if not rows:
        click.echo("(none missing)")
        return

    if as_json:
        payload = {
            "count": len(rows),
            "items": [{"asin": a, "kind": k, "variant": v} for a, k, v in rows],
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))
        return

    for asin, kind, variant in rows:
        click.echo(f"{asin}\t{kind}\t{variant}")
    click.echo(f"-- showing {len(rows)} missing (all synthesized) --", err=True)


# -----------------------------------------------------------------------------
# log-download
# -----------------------------------------------------------------------------

@assets.command("log-download", help="Append a download result for an asset.")
@click.option("--asset-id", type=int, default=None, help="Existing asset id. If not set, --asin/--kind is used.")
@click.option("--asin", default=None, help="Audible ASIN (used if --asset-id not given).")
@click.option(
    "--kind",
    type=click.Choice(["AUDIO", "COVER", "PDF", "CHAPTERS", "ANNOTATIONS"], case_sensitive=True),
    default=None,
    help="Asset kind (used if --asset-id not given).",
)
@click.option("--variant", default=None, help="Variant value (used if --asset-id not given).")
@click.option(
    "--status",
    required=False,
    type=click.Choice(["SUCCESS", "FAILED", "SKIPPED"], case_sensitive=True),
    help="Download result status. If omitted, the command runs in lookup mode and shows the latest download(s) for the selected asset(s).",
)
@click.option("--storage-path", type=click.Path(path_type=Path, dir_okay=False, resolve_path=True), default=None)
@click.option("--http-status", type=int, default=None)
@click.option("--bytes-written", type=int, default=None)
@click.option("--source-url", type=str, default=None)
@click.option("--etag", type=str, default=None)
@click.option("--error-message", type=str, default=None)
@pass_session
def cmd_log_download(
    session: Any,
    *,
    asset_id: Optional[int],
    asin: Optional[str],
    kind: Optional[AssetKindName],
    variant: Optional[str],
    status: Optional[DownloadStatus],
    storage_path: Optional[Path],
    http_status: Optional[int],
    bytes_written: Optional[int],
    source_url: Optional[str],
    etag: Optional[str],
    error_message: Optional[str],
) -> None:
    """Record a download attempt. If --asset-id is omitted, ensure the asset first using ASIN/KIND/VARIANT."""
    db_path = session.db_path_for("library")

    # If no status is provided, run in lookup mode: show the latest download row(s)
    # for the selected asset(s) instead of inserting a new log entry.
    if status is None:
        async def _lookup() -> list[dict[str, Any]]:
            async with open_db(db_path) as conn:
                await ensure_assets_schema(conn)
                filters = []
                params: list[Any] = []
                sql = """
                SELECT a.id AS asset_id,
                       a.asin,
                       k.name AS kind,
                       a.variant,
                       d.status,
                       d.bytes_written,
                       d.storage_path,
                       d.started_utc,
                       d.finished_utc
                FROM assets a
                JOIN asset_kind k ON k.id = a.kind_id
                LEFT JOIN v_asset_last_download d ON d.asset_id = a.id
                """
                if asset_id is not None:
                    filters.append("a.id = ?")
                    params.append(asset_id)
                if asin:
                    filters.append("a.asin = ?")
                    params.append(asin)
                if kind:
                    filters.append("k.name = ?")
                    params.append(kind)
                if variant:
                    filters.append("a.variant = ?")
                    params.append(variant)
                if filters:
                    sql += " WHERE " + " AND ".join(filters)
                sql += " ORDER BY a.asin, k.name, a.variant"

                cur = await conn.execute(sql, tuple(params))
                rows = await cur.fetchall()
                await cur.close()
                cols = ["asset_id", "asin", "kind", "variant", "status", "bytes_written", "storage_path", "started_utc", "finished_utc"]
                return [dict(zip(cols, r)) for r in rows]

        results = asyncio.run(_lookup())
        if not results:
            click.echo("(no matching assets)")
            return
        # Text output (one line per asset)
        for r in results:
            aid = r.get("asset_id")
            asin_s = r.get("asin") or ""
            kind_s = r.get("kind") or ""
            var_s = r.get("variant") or ""
            stat = r.get("status") or "-"
            size = r.get("bytes_written")
            size_s = str(size) if size is not None else "-"
            path = r.get("storage_path") or "-"
            start = r.get("started_utc") or "-"
            finish = r.get("finished_utc") or "-"
            click.echo(f"{aid}\t{asin_s}\t{kind_s}\t{var_s}\t{stat}\t{size_s}\t{path}\t{start}\t{finish}")
        return

    async def _work() -> int:
        async with open_db(db_path) as conn:
            await ensure_assets_schema(conn)
            aid = asset_id
            if aid is None:
                if not asin or not kind:
                    raise click.ClickException("When --asset-id is not provided, --asin and --kind are required.")
                # Ensure asset exists (creates or finds) to attach the download log.
                aid = await ensure_asset_async(
                    conn,
                    asin=asin,
                    kind=kind,  # type: ignore[arg-type]
                    variant=variant,
                    expected_path=storage_path,
                    meta=None,
                )
            return await log_download_async(
                conn,
                asset_id=aid,  # type: ignore[arg-type]
                status=status,
                storage_path=storage_path,
                http_status=http_status,
                bytes_written=bytes_written,
                source_url=source_url,
                etag=etag,
                error_message=error_message,
                autocommit=True,
            )

    row_id = asyncio.run(_work())
    click.echo(json.dumps({"download_id": row_id}, ensure_ascii=False))
