from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Final, Literal

import aiosqlite

from audible_cli.db import register_schema
from audible_cli.db import open_db

ASSET_SCHEMA_SQL: str = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS asset_kind (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
         CHECK (name IN ('AUDIO','COVER','PDF','CHAPTERS','ANNOTATIONS'))
);

INSERT OR IGNORE INTO asset_kind (name) VALUES
  ('AUDIO'), ('COVER'), ('PDF'), ('CHAPTERS'), ('ANNOTATIONS');

CREATE TABLE IF NOT EXISTS assets (
  id            INTEGER PRIMARY KEY,
  asin          TEXT NOT NULL,
  kind_id       INTEGER NOT NULL,
  variant       TEXT NOT NULL DEFAULT '',
  expected_path TEXT,
  meta_json     TEXT,
  created_utc   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_utc   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (asin, kind_id, variant),
  FOREIGN KEY (asin)    REFERENCES items(asin) ON DELETE CASCADE,
  FOREIGN KEY (kind_id) REFERENCES asset_kind(id) ON DELETE RESTRICT
);

CREATE TRIGGER IF NOT EXISTS trg_assets_touch
AFTER UPDATE ON assets
BEGIN
  UPDATE assets SET updated_utc = datetime('now') WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS downloads (
  id               INTEGER PRIMARY KEY,
  asset_id         INTEGER NOT NULL,
  started_utc      TEXT NOT NULL DEFAULT (datetime('now')),
  finished_utc     TEXT,
  status           TEXT NOT NULL CHECK (status IN ('SUCCESS','FAILED','SKIPPED')),
  http_status      INTEGER,
  bytes_written    INTEGER,
  checksum_sha256  TEXT,
  storage_path     TEXT,
  source_url       TEXT,
  etag             TEXT,
  error_message    TEXT,
  FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE VIEW IF NOT EXISTS v_asset_last_download AS
SELECT d.*
FROM downloads d
JOIN (
  SELECT asset_id, MAX(started_utc) AS max_started
  FROM downloads
  GROUP BY asset_id
) last ON last.asset_id = d.asset_id AND last.max_started = d.started_utc;

CREATE INDEX IF NOT EXISTS idx_assets_asin_kind_variant ON assets (asin, kind_id, variant);
CREATE INDEX IF NOT EXISTS idx_downloads_asset_time ON downloads (asset_id, started_utc DESC);
"""

AssetKindName = Literal["AUDIO", "COVER", "PDF", "CHAPTERS", "ANNOTATIONS"]
DownloadStatus = Literal["SUCCESS", "FAILED", "SKIPPED"]

AUDIO_VARIANTS: Final[set[str]] = {"AAX", "AAXC"}
CHAPTERS_VARIANTS: Final[set[str]] = {"flat", "tree"}


async def ensure_assets_schema(conn: aiosqlite.Connection) -> None:
    """Create/migrate the asset/download schema idempotently.

    Args:
        conn: Open aiosqlite connection.

    Returns:
        None
    """
    await conn.executescript(ASSET_SCHEMA_SQL)


# Registry: nach der Library (order=20)
register_schema("assets", ensure_assets_schema, order=20)


async def get_kind_id_async(conn: aiosqlite.Connection, kind: AssetKindName) -> int:
    """Resolve asset_kind.name to primary key.

    Args:
        conn: Open aiosqlite connection.
        kind: Logical asset kind ("AUDIO", "COVER", ...).

    Returns:
        Integer primary key.

    Raises:
        LookupError: If kind is unknown.
    """
    cur = await conn.execute("SELECT id FROM asset_kind WHERE name = ?;", (kind,))
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise LookupError(f"Unknown asset kind: {kind}")
    return int(row[0])


def _normalize_variant(kind: AssetKindName, variant: str | None) -> str:
    """Normalize/validate the variant string per kind.

    Args:
        kind: Asset kind.
        variant: Optional variant string.

    Returns:
        Normalized variant.

    Raises:
        ValueError: If a required variant is missing/invalid.
    """
    if kind == "AUDIO":
        v = (variant or "").strip()
        if v not in AUDIO_VARIANTS:
            raise ValueError("AUDIO variant must be 'AAX' or 'AAXC'.")
        return v
    if kind == "COVER":
        v = (variant or "").strip()
        if not v:
            raise ValueError("COVER variant must be a non-empty resolution string (e.g., '900').")
        return v
    if kind == "PDF":
        return (variant or "").strip()
    if kind == "CHAPTERS":
        v = (variant or "").strip()
        if v not in CHAPTERS_VARIANTS:
            raise ValueError("CHAPTERS variant must be 'flat' or 'tree'.")
        return v
    if kind == "ANNOTATIONS":
        return (variant or "").strip()
    raise ValueError(f"Unsupported asset kind: {kind}")


async def ensure_asset_async(
    conn: aiosqlite.Connection,
    *,
    asin: str,
    kind: AssetKindName,
    variant: str | None = None,
    expected_path: Path | None = None,
    meta: dict | None = None,
) -> int:
    """Ensure an (asin, kind, variant) asset row exists and return its id.

    Defaults:
      - PDF/ANNOTATIONS: variant defaults to "".
      - AUDIO:           'AAX' | 'AAXC' required.
      - COVER:           resolution string required.
      - CHAPTERS:        'flat' | 'tree' required.

    Args:
        conn: Open aiosqlite connection (transaction managed by caller).
        asin: Audible ASIN.
        kind: Asset kind.
        variant: Variant per kind.
        expected_path: Intended storage target.
        meta: Optional metadata dict (stored as JSON).

    Returns:
        Primary key of the asset row.
    """
    norm_variant = _normalize_variant(kind, variant)
    kind_id = await get_kind_id_async(conn, kind)
    cur = await conn.execute(
        """
        INSERT INTO assets (asin, kind_id, variant, expected_path, meta_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(asin, kind_id, variant) DO UPDATE SET
          expected_path = COALESCE(excluded.expected_path, assets.expected_path),
          meta_json     = COALESCE(excluded.meta_json, assets.meta_json)
        RETURNING id;
        """,
        (
            asin,
            kind_id,
            norm_variant,
            str(expected_path) if expected_path else None,
            json.dumps(meta, ensure_ascii=False) if meta else None,
        ),
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0])


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 checksum of a file.

    Args:
        path: File path.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def log_download_async(
    conn: aiosqlite.Connection,
    *,
    asset_id: int,
    status: DownloadStatus,
    storage_path: Path | None = None,
    http_status: int | None = None,
    bytes_written: int | None = None,
    source_url: str | None = None,
    etag: str | None = None,
    error_message: str | None = None,
    autocommit: bool = False,
) -> int:
    """Append a download attempt to the downloads table.

    Computing the checksum is off-loaded to a worker thread to keep the event loop responsive.
    Optionally commits the transaction before returning.

    Args:
        conn: Open aiosqlite connection (transaction managed by caller unless ``autocommit=True``).
        asset_id: Foreign key to ``assets.id``.
        status: ``'SUCCESS'`` | ``'FAILED'`` | ``'SKIPPED'``.
        storage_path: Final file path if applicable.
        http_status: HTTP response status.
        bytes_written: Final size in bytes.
        source_url: Source URL used for the download.
        etag: HTTP ETag header value.
        error_message: Error message for failures.
        autocommit: If ``True``, call ``conn.commit()`` before returning.

    Returns:
        Primary key of the inserted ``downloads`` row.

    Raises:
        aiosqlite.Error: On SQL/constraint issues.
    """
    checksum: str | None = None
    if status == "SUCCESS" and storage_path is not None:
        # Off-thread hashing keeps the event loop snappy.
        checksum = await asyncio.to_thread(_sha256_file, storage_path)

    async with conn.execute(
        """
        INSERT INTO downloads (
          asset_id, finished_utc, status, http_status, bytes_written,
          checksum_sha256, storage_path, source_url, etag, error_message
        )
        VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id;
        """,
        (
            asset_id,
            status,
            http_status,
            bytes_written,
            checksum,
            str(storage_path) if storage_path else None,
            source_url,
            etag,
            error_message,
        ),
    ) as cur:
        row = await cur.fetchone()
        if row is None:
            raise aiosqlite.Error("INSERT RETURNING yielded no row")
        insert_id = int(row[0])

    if autocommit:
        await conn.commit()

    return insert_id


async def get_asset_matrix_for_asin_async(conn: aiosqlite.Connection, asin: str) -> list[dict]:
    """Return latest status per asset (kind+variant) for a given ASIN.

    Args:
        conn: Open aiosqlite connection.
        asin: Audible ASIN.

    Returns:
        List of dicts: {kind, variant, status, storage_path, bytes_written}
    """
    sql = """
    SELECT k.name AS kind, a.variant, d.status, d.storage_path, d.bytes_written
    FROM assets a
    JOIN asset_kind k ON k.id = a.kind_id
    LEFT JOIN v_asset_last_download d ON d.asset_id = a.id
    WHERE a.asin = ?
    ORDER BY k.name, a.variant;
    """
    cur = await conn.execute(sql, (asin,))
    rows = await cur.fetchall()
    await cur.close()
    cols = ["kind", "variant", "status", "storage_path", "bytes_written"]
    return [dict(zip(cols, r)) for r in rows]


async def list_missing_assets_async(conn: aiosqlite.Connection, limit: int = 100) -> list[tuple[str, str, str]]:
    """List assets that do not have a successful download yet.

    Args:
        conn: Open aiosqlite connection.
        limit: Max rows to return.

    Returns:
        List of tuples (asin, kind, variant).
    """
    sql = """
    SELECT a.asin, k.name AS kind, a.variant
    FROM assets a
    JOIN asset_kind k ON k.id = a.kind_id
    LEFT JOIN v_asset_last_download d
      ON d.asset_id = a.id AND d.status = 'SUCCESS'
    WHERE d.id IS NULL
    ORDER BY a.asin, k.name, a.variant
    LIMIT ?;
    """
    cur = await conn.execute(sql, (limit,))
    rows = await cur.fetchall()
    await cur.close()
    return [(r[0], r[1], r[2]) for r in rows]


async def upsert_asset_async(
    db_path: Path,
    *,
    asin: str,
    kind: AssetKindName,
    variant: str | None = None,
    expected_path: Path | None = None,
    meta: dict | None = None,
) -> int:
    """Convenience wrapper to create/update an asset using a managed connection.

    Args:
        db_path: SQLite database file.
        asin: Audible ASIN.
        kind: Asset kind name.
        variant: Variant value per kind.
        expected_path: Intended storage path.
        meta: Optional JSON-serializable metadata dict.

    Returns:
        Primary key of the asset row.
    """
    async with open_db(db_path) as conn:
        await ensure_assets_schema(conn)
        await conn.execute("BEGIN IMMEDIATE;")
        try:
            rid = await ensure_asset_async(
                conn,
                asin=asin,
                kind=kind,
                variant=variant,
                expected_path=expected_path,
                meta=meta,
            )
            await conn.commit()
            return rid
        except Exception:
            await conn.rollback()
            raise


async def list_assets_async(
    db_path: Path,
    *,
    asin: str | None = None,
    kind: AssetKindName | None = None,
    variant: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """List assets (optionally filtered) with last download info.

    Args:
        db_path: SQLite database file.
        asin: Optional ASIN filter.
        kind: Optional asset kind filter.
        variant: Optional variant filter.
        limit: Max rows to return.
        offset: Offset for paging.

    Returns:
        List of dict rows with fields: id, asin, kind, variant, expected_path,
        meta_json, last_status, storage_path, bytes_written, updated_utc.
    """
    where: list[str] = []
    args: list[object] = []

    if asin:
        where.append("a.asin = ?")
        args.append(asin)
    if kind:
        where.append("k.name = ?")
        args.append(kind)
    if variant is not None:
        where.append("a.variant = ?")
        args.append(variant)

    sql = (
        "SELECT a.id, a.asin, k.name AS kind, a.variant, a.expected_path, a.meta_json, "
        "d.status AS last_status, d.storage_path, d.bytes_written, a.updated_utc "
        "FROM assets a "
        "JOIN asset_kind k ON k.id = a.kind_id "
        "LEFT JOIN v_asset_last_download d ON d.asset_id = a.id "
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY a.updated_utc DESC, a.id DESC LIMIT ? OFFSET ?"
    args.extend([int(limit), int(offset)])

    async with open_db(db_path) as conn:
        await ensure_assets_schema(conn)
        cur = await conn.execute(sql, tuple(args))
        rows = await cur.fetchall()
        await cur.close()

    cols = [
        "id",
        "asin",
        "kind",
        "variant",
        "expected_path",
        "meta_json",
        "last_status",
        "storage_path",
        "bytes_written",
        "updated_utc",
    ]
    out: list[dict] = [dict(zip(cols, r)) for r in rows]

    # Optionally parse meta_json into a dict for convenience
    for r in out:
        mj = r.get("meta_json")
        if isinstance(mj, str) and mj:
            try:
                r["meta"] = json.loads(mj)
            except Exception:
                r["meta"] = None
    return out
