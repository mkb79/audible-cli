from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

import aiosqlite
from filelock import FileLock

# ------------------ Schema & SQL ------------------

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS items (
  asin        TEXT PRIMARY KEY,
  doc         TEXT NOT NULL,       -- full original JSON (current version)
  title       TEXT NOT NULL,       -- extracted from JSON
  subtitle    TEXT,                -- may be NULL
  full_title  TEXT NOT NULL,       -- title[: subtitle]
  updated_utc TEXT NOT NULL,       -- last upsert time
  is_deleted  INTEGER NOT NULL DEFAULT 0,
  deleted_utc TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  response_groups TEXT NOT NULL,       -- the one-and-only groups string for this DB
  last_state_token_utc TEXT,           -- last sync token (ISO-8601 UTC)
  last_state_token_raw TEXT,           -- raw token as received (e.g., epoch ms)
  created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  request_time_utc          TEXT NOT NULL,
  request_state_token_utc   TEXT,
  response_time_utc         TEXT NOT NULL,
  response_state_token_utc  TEXT,
  http_status               INTEGER,
  num_upserted              INTEGER DEFAULT 0,
  num_soft_deleted          INTEGER DEFAULT 0,
  note                      TEXT,
  upserted_asins            TEXT,
  soft_deleted_asins        TEXT
);
    
-- View of active items with derived fields from JSON
CREATE VIEW IF NOT EXISTS v_books AS
SELECT
  asin,
  title,
  subtitle,
  full_title,
  COALESCE(
    json_extract(doc, '$.purchase_date'),
    json_extract(doc, '$.library_status.date_added')
  ) AS purchase_date,
  COALESCE(
    json_extract(doc, '$.language'),
    json_extract(doc, '$.metadata.language')
  ) AS language,
  COALESCE(
    json_extract(doc, '$.runtime_length_min'),
    json_extract(doc, '$.duration_min')
  ) AS runtime_min,
  json_extract(doc, '$.is_ayce') AS is_ayce
FROM items
WHERE is_deleted = 0;

-- Indices
CREATE INDEX IF NOT EXISTS idx_items_title       ON items (lower(title));
CREATE INDEX IF NOT EXISTS idx_items_subtitle    ON items (lower(subtitle));
CREATE INDEX IF NOT EXISTS idx_items_full_title  ON items (lower(full_title));
CREATE INDEX IF NOT EXISTS idx_items_is_deleted  ON items (is_deleted);
"""

# Indices continued (split to avoid editor confusion)
SCHEMA_SQL += r"""
CREATE INDEX IF NOT EXISTS idx_items_purchase    ON items (
  COALESCE(json_extract(doc,'$.purchase_date'),
           json_extract(doc,'$.library_status.date_added'))
);
CREATE INDEX IF NOT EXISTS idx_items_language    ON items (
  COALESCE(json_extract(doc,'$.language'),
           json_extract(doc,'$.metadata.language'))
);
"""

UPSERT_SQL = """
INSERT INTO items(asin, doc, title, subtitle, full_title, updated_utc, is_deleted, deleted_utc)
VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
ON CONFLICT(asin) DO UPDATE SET
  doc         = excluded.doc,
  title       = excluded.title,
  subtitle    = excluded.subtitle,
  full_title  = excluded.full_title,
  updated_utc = excluded.updated_utc,
  is_deleted  = 0,
  deleted_utc = NULL;
"""

SOFT_DELETE_SQL = """
UPDATE items
SET is_deleted = 1,
    deleted_utc = ?,
    updated_utc = ?
WHERE asin = ?;
"""

INSERT_SYNC_LOG_SQL = """
INSERT INTO sync_log(
  request_time_utc,
  request_state_token_utc,
  response_time_utc,
  response_state_token_utc,
  http_status,
  num_upserted,
  num_soft_deleted,
  note,
  upserted_asins,
  soft_deleted_asins
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# FTS5 schema & triggers (external content indexing of 'items')
FTS_SQL = r"""
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  full_title,
  title,
  subtitle,
  asin UNINDEXED,
  content='items',
  content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS trg_items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, full_title, title, subtitle, asin)
  VALUES (new.rowid, new.full_title, new.title, new.subtitle, new.asin);
END;

CREATE TRIGGER IF NOT EXISTS trg_items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, full_title, title, subtitle, asin)
  VALUES('delete', old.rowid, old.full_title, old.title, old.subtitle, old.asin);
END;

CREATE TRIGGER IF NOT EXISTS trg_items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, full_title, title, subtitle, asin)
  VALUES('delete', old.rowid, old.full_title, old.title, old.subtitle, old.asin);
  INSERT INTO items_fts(rowid, full_title, title, subtitle, asin)
  VALUES (new.rowid, new.full_title, new.title, new.subtitle, new.asin);
END;
"""

SOFT_DELETED_LIST_SQL = """
SELECT asin, title, subtitle, full_title, deleted_utc, updated_utc
FROM items
WHERE is_deleted = 1
ORDER BY COALESCE(deleted_utc, updated_utc) DESC, asin
LIMIT ? OFFSET ?;
"""

SOFT_DELETED_COUNT_SQL = "SELECT COUNT(*) FROM items WHERE is_deleted = 1;"


# ------------------ Async FileLock wrapper ------------------

class AsyncFileLock:
    """Async context manager wrapping filelock.FileLock via a thread to keep the loop non-blocking."""

    def __init__(self, path: Path, timeout: float = 30.0):
        self._lock = FileLock(str(path), timeout=timeout)

    async def __aenter__(self):
        await asyncio.to_thread(self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await asyncio.to_thread(self._lock.release)


# ------------------ Helpers ------------------

def now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_ms_to_iso(token: Optional[str | int | float]) -> Tuple[Optional[str], Optional[str]]:
    if token is None:
        return None, None
    raw = str(token).strip()
    try:
        val = int(raw)
        seconds = val / 1000.0 if val > 10_000_000_000 else float(val)
        dt = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), raw
    except Exception:
        return None, raw


def build_full_title(record: dict) -> str:
    """Return 'Title: Subtitle' (Subtitle optional). Title must be non-empty."""
    title = record.get("title")
    if not title or not str(title).strip():
        raise ValueError(f"Missing or empty title in record with asin={record.get('asin')}")
    title_s = str(title).strip()
    subtitle = record.get("subtitle")
    if subtitle and str(subtitle).strip():
        return f"{title_s}: {str(subtitle).strip()}"
    return title_s


def should_soft_delete_by_visibility(record: dict) -> bool:
    """
    Return True iff library_status.is_visible is explicitly False.
    If library_status is missing or None, returns False (no action).
    """
    ls = record.get("library_status")
    if isinstance(ls, dict):
        return ls.get("is_visible") is False
    return False


def normalize_items(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    return payload if isinstance(payload, list) else []


def extract_removed_asins(payload: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(payload, dict):
        for key in ("removed_asins", "deleted_asins", "revoked_asins"):
            seq = payload.get(key)
            if isinstance(seq, list):
                out.update(str(x) for x in seq if x)
        for key in ("deleted_items", "removed_items", "revoked_items"):
            seq = payload.get(key)
            if isinstance(seq, list):
                for obj in seq:
                    asin = (obj or {}).get("asin")
                    if asin:
                        out.add(str(asin))
    return out


# ------------------ Connection helper ------------------

@asynccontextmanager
async def open_db(db_path: Path):
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
    finally:
        await conn.close()


async def ensure_schema(conn: aiosqlite.Connection) -> None:
    """Create a baseline schema and indices. Run lightweight migration for title/subtitle/full_title if missing, then FTS."""
    await conn.executescript(SCHEMA_SQL)

    async def _have_col(table: str, column: str) -> bool:
        cur = await conn.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in await cur.fetchall()]
        await cur.close()
        return column in cols

    changed = False
    if not await _have_col("items", "title"):
        await conn.execute("ALTER TABLE items ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        await conn.execute("UPDATE items SET title = COALESCE(json_extract(doc, '$.title'), '') WHERE title = ''")
        changed = True
    if not await _have_col("items", "subtitle"):
        await conn.execute("ALTER TABLE items ADD COLUMN subtitle TEXT")
        await conn.execute("UPDATE items SET subtitle = json_extract(doc, '$.subtitle') WHERE subtitle IS NULL")
        changed = True
    if not await _have_col("items", "full_title"):
        await conn.execute("ALTER TABLE items ADD COLUMN full_title TEXT NOT NULL DEFAULT ''")
        cur = await conn.execute("SELECT asin, doc FROM items")
        rows = await cur.fetchall()
        await cur.close()
        for asin, doc_json in rows:
            try:
                r = json.loads(doc_json)
                ft = build_full_title(r)
            except Exception:
                t = json.loads(doc_json).get("title") or ""
                ft = str(t).strip()
            await conn.execute("UPDATE items SET full_title=? WHERE asin=?", (ft, asin))
        changed = True

    if changed:
        await conn.commit()

    # Ensure FTS exists (triggers included)
    await conn.executescript(FTS_SQL)

    # Ensure (or re-create) secondary indices (idempotent)
    await conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_items_title       ON items (lower(title));
    CREATE INDEX IF NOT EXISTS idx_items_subtitle    ON items (lower(subtitle));
    CREATE INDEX IF NOT EXISTS idx_items_full_title  ON items (lower(full_title));
    CREATE INDEX IF NOT EXISTS idx_items_is_deleted  ON items (is_deleted);
    CREATE INDEX IF NOT EXISTS idx_items_purchase    ON items (
      COALESCE(json_extract(doc,'$.purchase_date'), json_extract(doc,'$.library_status.date_added'))
    );
    CREATE INDEX IF NOT EXISTS idx_items_language    ON items (
      COALESCE(json_extract(doc,'$.language'), json_extract(doc,'$.metadata.language'))
    );
    """)


async def ensure_initialized_async(
    db_path: Path,
    response_groups: str,
) -> None:
    """
    Ensure the database file exists, schema is created, and settings row (id=1) is present.
    Safe to call repeatedly.
    """
    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_schema(conn)
            await conn.execute("BEGIN IMMEDIATE;")
            await init_or_check_settings(conn, response_groups)
            await conn.commit()


async def init_or_check_settings(
    conn: aiosqlite.Connection,
    response_groups: str,
) -> None:
    cur = await conn.execute("SELECT response_groups FROM settings WHERE id=1")
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        await conn.execute(
            "INSERT INTO settings(id, response_groups, created_utc) VALUES (1, ?, ?)",
            (response_groups, now_iso_utc()),
        )
    else:
        (existing,) = row
        if existing != response_groups:
            raise RuntimeError(
                f"This DB was created with response_groups='{existing}', "
                f"but you passed '{response_groups}'. Use a new DB or align the value."
            )

# ------------------ WRITE paths (Filelock before any read) ------------------

async def init_db_async(db_path: Path, response_groups: str) -> None:
    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_schema(conn)
            await conn.execute("BEGIN IMMEDIATE;")
            await init_or_check_settings(conn, response_groups)
            await conn.commit()


async def full_import_async(
    db_path: Path,
    payload: Any,
    *,
    response_token: Optional[str | int | float],
    note: Optional[str],
) -> int:
    items = normalize_items(payload)
    resp_iso, resp_raw = epoch_ms_to_iso(response_token)

    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_schema(conn)
            cur = await conn.execute("SELECT 1 FROM settings WHERE id=1")
            have = await cur.fetchone()
            await cur.close()
            if have is None:
                raise RuntimeError("Settings not initialized. Run 'db library init' first with --response-groups.")

            await conn.execute("BEGIN IMMEDIATE;")

            ts = now_iso_utc()
            batch = []
            to_soft_delete_by_visibility: set[str] = set()

            for r in items:
                asin = r.get("asin")
                if not asin:
                    continue
                title = (r.get("title") or "").strip()
                if not title:
                    raise ValueError(f"Empty title for asin={asin}")
                subtitle = r.get("subtitle")
                full_title = build_full_title(r)
                doc = json.dumps(r, ensure_ascii=False)
                batch.append((asin, doc, title, subtitle, full_title, ts))

                if should_soft_delete_by_visibility(r):
                    to_soft_delete_by_visibility.add(asin)

            if batch:
                await conn.executemany(UPSERT_SQL, batch)
            num_upserted = len(batch)

            # soft-deletes by visibility=false
            num_deleted_vis = 0
            if to_soft_delete_by_visibility:
                ts_del = now_iso_utc()
                for asin in to_soft_delete_by_visibility:
                    await conn.execute(SOFT_DELETE_SQL, (ts_del, ts_del, asin))
                num_deleted_vis = len(to_soft_delete_by_visibility)

            if resp_iso or resp_raw:
                await conn.execute(
                    """
                    UPDATE settings
                       SET last_state_token_utc = COALESCE(?, last_state_token_utc),
                           last_state_token_raw = COALESCE(?, last_state_token_raw)
                     WHERE id = 1
                    """,
                    (resp_iso, resp_raw),
                )

            note_parts = [note] if note else []
            note_parts.append(f"vis_soft_deleted={num_deleted_vis}")

            upserted_asins_list = [t[0] for t in batch]
            soft_deleted_asins_list = sorted(to_soft_delete_by_visibility)

            await conn.execute(
                INSERT_SYNC_LOG_SQL,
                (
                    now_iso_utc(),  # request_time_utc
                    None,  # request_state_token_utc (full-sync → None)
                    now_iso_utc(),  # response_time_utc
                    resp_iso,  # response_state_token_utc
                    200,  # http_status
                    num_upserted,  # num_upserted
                    num_deleted_vis,  # num_soft_deleted
                    " | ".join(n for n in note_parts if n),  # note
                    json.dumps(upserted_asins_list, ensure_ascii=False),  # upserted_asins
                    json.dumps(soft_deleted_asins_list, ensure_ascii=False),  # soft_deleted_asins
                ),
            )

            await conn.commit()
            return num_upserted


async def delta_import_async(
    db_path: Path,
    payload: Any,
    *,
    request_token: Optional[str | int | float],
    response_token: Optional[str | int | float],
    note: Optional[str],
) -> tuple[int, int]:
    items = normalize_items(payload)
    removed = extract_removed_asins(payload)

    req_iso, req_raw = epoch_ms_to_iso(request_token)
    resp_iso, resp_raw = epoch_ms_to_iso(response_token)

    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_schema(conn)
            cur = await conn.execute("SELECT 1 FROM settings WHERE id=1")
            have = await cur.fetchone()
            await cur.close()
            if have is None:
                raise RuntimeError("Settings not initialized. Run 'db library init' first with --response-groups.")

            await conn.execute("BEGIN IMMEDIATE;")

            ts = now_iso_utc()
            batch = []
            to_soft_delete_by_visibility: set[str] = set()

            for r in items:
                asin = r.get("asin")
                if not asin:
                    continue
                title = (r.get("title") or "").strip()
                if not title:
                    raise ValueError(f"Empty title for asin={asin}")
                subtitle = r.get("subtitle")
                full_title = build_full_title(r)
                doc = json.dumps(r, ensure_ascii=False)
                batch.append((asin, doc, title, subtitle, full_title, ts))

                if should_soft_delete_by_visibility(r):
                    to_soft_delete_by_visibility.add(asin)

            if batch:
                await conn.executemany(UPSERT_SQL, batch)
            num_upserted = len(batch)

            # combine server removals + visibility-based soft-deletes
            num_deleted_removed = len(removed)
            num_deleted_vis = len(to_soft_delete_by_visibility)
            to_soft_delete_all = set(removed) | to_soft_delete_by_visibility
            soft_deleted_asins_list = sorted(to_soft_delete_all)

            num_deleted_total = 0
            if to_soft_delete_all:
                ts_del = now_iso_utc()
                for asin in to_soft_delete_all:
                    await conn.execute(SOFT_DELETE_SQL, (ts_del, ts_del, asin))
                num_deleted_total = len(to_soft_delete_all)

            if resp_iso or req_iso:
                await conn.execute(
                    """
                    UPDATE settings
                       SET last_state_token_utc = COALESCE(?, last_state_token_utc),
                           last_state_token_raw = COALESCE(?, last_state_token_raw)
                     WHERE id = 1
                    """,
                    (resp_iso or req_iso, resp_raw or req_raw),
                )

            note_parts = [note] if note else []
            if req_iso is None and req_raw:
                note_parts.append(f"request_token_raw={req_raw}")
            if resp_iso is None and resp_raw:
                note_parts.append(f"response_token_raw={resp_raw}")
            note_parts.append(f"removed_asins={num_deleted_removed}")
            note_parts.append(f"vis_soft_deleted={num_deleted_vis}")

            upserted_asins_list = [t[0] for t in batch]
            soft_deleted_asins_list = sorted(to_soft_delete_all)

            await conn.execute(
                INSERT_SYNC_LOG_SQL,
                (
                    now_iso_utc(),  # request_time_utc
                    req_iso,  # request_state_token_utc
                    now_iso_utc(),  # response_time_utc
                    resp_iso,  # response_state_token_utc
                    200,  # http_status
                    num_upserted,  # num_upserted
                    num_deleted_total,  # num_soft_deleted
                    " | ".join(n for n in note_parts if n),  # note
                    json.dumps(upserted_asins_list, ensure_ascii=False),  # upserted_asins
                    json.dumps(soft_deleted_asins_list, ensure_ascii=False),
                # soft_deleted_asins
                ),
            )

            await conn.commit()
            return num_upserted, num_deleted_total

# ------------------ READ-ONLY helpers (no file lock) ------------------

async def query_search_async(db_path: Path, needle: str, limit: int = 20) -> list[tuple[str, str]]:
    """LIKE-based search across title, subtitle, full_title (case-insensitive)."""
    async with open_db(db_path) as conn:
        sql = """
        SELECT asin, full_title
        FROM items
        WHERE is_deleted = 0
          AND (
            lower(title)      LIKE '%' || ? || '%'
            OR lower(subtitle)   LIKE '%' || ? || '%'
            OR lower(full_title) LIKE '%' || ? || '%'
          )
        ORDER BY full_title
        LIMIT ?
        """
        cur = await conn.execute(sql, (needle.lower(), needle.lower(), needle.lower(), limit))
        rows = await cur.fetchall()
        await cur.close()
        return [(r[0], r[1]) for r in rows]


async def query_search_fts_async(db_path: Path, query: str, limit: int = 20) -> list[tuple[str, str]]:
    """
    FTS5 search across full_title/title/subtitle with ranking (bm25 if available).
    Falls back to LIKE if the MATCH query fails (syntax).
    """
    q = (query or "").strip()
    if not q:
        return []

    async with open_db(db_path) as conn:
        sql = """
        SELECT i.asin, i.full_title,
               CASE WHEN 1 THEN bm25(items_fts) ELSE 0 END AS score
        FROM items_fts
        JOIN items AS i ON i.rowid = items_fts.rowid
        WHERE i.is_deleted = 0
          AND items_fts MATCH ?
        ORDER BY score, i.full_title
        LIMIT ?
        """
        try:
            cur = await conn.execute(sql, (q, limit))
            rows = await cur.fetchall()
            await cur.close()
            return [(r[0], r[1]) for r in rows]
        except Exception:
            cur = await conn.execute(
                """
                SELECT asin, full_title
                FROM items
                WHERE is_deleted = 0
                  AND (lower(full_title) LIKE '%' || ? || '%'
                       OR lower(title) LIKE '%' || ? || '%'
                       OR lower(subtitle) LIKE '%' || ? || '%')
                ORDER BY full_title
                LIMIT ?
                """,
                (q.lower(), q.lower(), q.lower(), limit),
            )
            rows = await cur.fetchall()
            await cur.close()
            return [(r[0], r[1]) for r in rows]


async def rebuild_fts_async(db_path: Path) -> None:
    """Rebuild the FTS index from 'items' content table."""
    async with open_db(db_path) as conn:
        await conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
        await conn.commit()


async def explain_query_async(db_path: Path, sql: str, params: tuple = ()) -> list[str]:
    """Run EXPLAIN QUERY PLAN on the given SQL (with optional params)."""
    async with open_db(db_path) as conn:
        cur = await conn.execute("EXPLAIN QUERY PLAN " + sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return [r[-1] for r in rows]


async def get_docs_by_asins(
    db_path: Path,
    asins: list[str],
    *,
    include_deleted: bool = False,
) -> list[tuple[str, str, str]]:
    """
    Return list of (asin, full_title, doc) for the given ASINs (exact match).
    If include_deleted=False, only active items (is_deleted=0) are returned.
    """
    if not asins:
        return []
    placeholders = ",".join("?" for _ in asins)
    where_deleted = "" if include_deleted else "AND is_deleted = 0"
    sql = f"""
        SELECT asin, full_title, doc
        FROM items
        WHERE asin IN ({placeholders})
          {where_deleted}
        ORDER BY full_title
    """
    async with open_db(db_path) as conn:
        cur = await conn.execute(sql, tuple(asins))
        rows = await cur.fetchall()
        await cur.close()
        return [(r[0], r[1], r[2]) for r in rows]


async def get_docs_by_titles(
    db_path: Path,
    titles: list[str],
    *,
    use_fts: bool = False,
    limit_per: int = 5,
    include_deleted: bool = False,
) -> list[tuple[str, str, str]]:
    """
    Return the list of (asin, full_title, doc) by title needles.
    De-duplicates by ASIN across needles.
    """
    if not titles:
        return []

    def to_prefix_match(q: str) -> str:
        parts: list[str] = []
        for tok in q.strip().split():
            tok = tok.strip()
            if not tok:
                continue
            if any(op in tok for op in ('"', "'", " NEAR/", " AND ", " OR ", " NOT ", "(", ")")):
                parts.append(tok)
            else:
                if not tok.endswith("*"):
                    tok = tok + "*"
                parts.append(tok)
        return " ".join(parts) if parts else q

    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    async with open_db(db_path) as conn:
        for raw in titles:
            needle = (raw or "").strip()
            if not needle:
                continue

            rows: list[tuple] = []

            if use_fts:
                match_q = to_prefix_match(needle)
                sql_fts = """
                    SELECT i.asin, i.full_title, i.doc
                    FROM items_fts
                    JOIN items AS i ON i.rowid = items_fts.rowid
                    WHERE {deleted_filter}
                      AND items_fts MATCH ?
                    ORDER BY i.full_title
                    LIMIT ?
                """.format(
                    deleted_filter="1=1" if include_deleted else "i.is_deleted = 0"
                )
                try:
                    cur = await conn.execute(sql_fts, (match_q, limit_per))
                    rows = await cur.fetchall()
                    await cur.close()
                except Exception:
                    rows = []

            if not use_fts or not rows:
                sql_like = """
                    SELECT asin, full_title, doc
                    FROM items
                    WHERE {deleted_filter}
                      AND (
                          lower(title)      LIKE '%' || ? || '%'
                       OR lower(subtitle)   LIKE '%' || ? || '%'
                       OR lower(full_title) LIKE '%' || ? || '%'
                      )
                    ORDER BY full_title
                    LIMIT ?
                """.format(
                    deleted_filter="1=1" if include_deleted else "is_deleted = 0"
                )
                n = needle.lower()
                cur = await conn.execute(sql_like, (n, n, n, limit_per))
                rows = await cur.fetchall()
                await cur.close()

            for asin, full_title, doc in rows:
                if asin in seen:
                    continue
                seen.add(asin)
                results.append((asin, full_title, doc))

    return results


async def get_settings_async(db_path: Path) -> dict | None:
    """Return settings row (id=1) as a dict or None if missing."""
    async with open_db(db_path) as conn:
        cur = await conn.execute(
            """
            SELECT response_groups,
                   last_state_token_raw,
                   last_state_token_utc
              FROM settings
             WHERE id=1
            """
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "response_groups": row[0],
            "last_state_token_raw": row[1],
            "last_state_token_utc": row[2],
        }


async def export_library_async(
    db_path: Path,
    *,
    include_deleted: bool = False,
    include_groups: bool = True,
    include_state_token: bool = True,
) -> dict:
    """
    Export the current library contents back into a dict.
    """
    async with open_db(db_path) as conn:
        where = "" if include_deleted else "WHERE is_deleted = 0"
        cur = await conn.execute(f"SELECT doc FROM items {where}")
        rows = await cur.fetchall()
        await cur.close()

        resp_groups_raw = None
        last_state_token_raw = None
        if include_groups or include_state_token:
            cur = await conn.execute(
                "SELECT response_groups, last_state_token_raw FROM settings WHERE id=1"
            )
            row = await cur.fetchone()
            await cur.close()
            if row:
                resp_groups_raw, last_state_token_raw = row

    items = [json.loads(doc) for (doc,) in rows if doc]

    result: dict = {"items": items}

    if include_groups:
        rg = (resp_groups_raw or "").strip()
        if rg.startswith("["):
            try:
                parsed = json.loads(rg)
                resp_groups = [str(x) for x in parsed if str(x).strip()]
            except Exception:
                resp_groups = [s.strip() for s in rg.split(",") if s.strip()]
        else:
            resp_groups = [s.strip() for s in rg.split(",") if s.strip()]
        result["response_groups"] = resp_groups

    if include_state_token and (last_state_token_raw is not None):
        result["state_token"] = str(last_state_token_raw)

    return result


async def list_soft_deleted_async(db_path: Path, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """
    Returns (rows, total), where rows is a list of dicts:
      {asin, title, subtitle, full_title, deleted_utc, updated_utc}
    """
    async with open_db(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
        )
        if await cur.fetchone() is None:
            await cur.close()
            raise RuntimeError("Library DB not initialized (missing 'items' table').")
        await cur.close()

        cur = await conn.execute(SOFT_DELETED_LIST_SQL, (limit, offset))
        rows_raw = await cur.fetchall()
        await cur.close()

        cur = await conn.execute(SOFT_DELETED_COUNT_SQL)
        total = int((await cur.fetchone())[0])
        await cur.close()

        cols = ["asin", "title", "subtitle", "full_title", "deleted_utc", "updated_utc"]
        rows = [dict(zip(cols, r)) for r in rows_raw]
        return rows, total


async def list_sync_logs_async(
    db_path: Path,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> tuple[list[dict], int]:
    """
    Return (rows, total) from sync_log.
    Each row is:
      {
        "id": int,
        "request_time_utc": str,
        "request_state_token_utc": Optional[str],
        "response_time_utc": str,
        "response_state_token_utc": Optional[str],
        "http_status": int,
        "num_upserted": int,
        "num_soft_deleted": int,
        "note": Optional[str],
        "upserted_asins": Optional[list[str]],      # parsed from JSON text (may be None)
        "soft_deleted_asins": Optional[list[str]],  # parsed from JSON text (may be None)
      }
    """
    sort = "DESC" if (order or "").lower() != "asc" else "ASC"
    async with open_db(db_path) as conn:
        # ensure table exists (leeres DB-File / noch kein init)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_log'"
        )
        if await cur.fetchone() is None:
            await cur.close()
            return [], 0
        await cur.close()

        # Prüfe optionale JSON-Spalten (Migrationen rückwärtskompatibel)
        cur = await conn.execute("PRAGMA table_info(sync_log)")
        cols_info = await cur.fetchall()
        await cur.close()
        have_up = any(c[1] == "upserted_asins" for c in cols_info)
        have_del = any(c[1] == "soft_deleted_asins" for c in cols_info)

        select_cols = [
            "id",
            "request_time_utc",
            "request_state_token_utc",
            "response_time_utc",
            "response_state_token_utc",
            "http_status",
            "num_upserted",
            "num_soft_deleted",
            "note",
        ]
        if have_up:
            select_cols.append("upserted_asins")
        else:
            select_cols.append("NULL AS upserted_asins")
        if have_del:
            select_cols.append("soft_deleted_asins")
        else:
            select_cols.append("NULL AS soft_deleted_asins")

        sql = f"""
            SELECT {", ".join(select_cols)}
            FROM sync_log
            ORDER BY id {sort}
            LIMIT ? OFFSET ?
        """
        cur = await conn.execute(sql, (limit, offset))
        rows = await cur.fetchall()
        await cur.close()

        cur = await conn.execute("SELECT COUNT(*) FROM sync_log")
        total = int((await cur.fetchone())[0])
        await cur.close()

        cols = [
            "id",
            "request_time_utc",
            "request_state_token_utc",
            "response_time_utc",
            "response_state_token_utc",
            "http_status",
            "num_upserted",
            "num_soft_deleted",
            "note",
            "upserted_asins",
            "soft_deleted_asins",
        ]

        def _parse_json_list(txt: str | None):
            if not txt:
                return None
            try:
                val = json.loads(txt)
                return val if isinstance(val, list) else None
            except Exception:
                return None

        out: list[dict] = []
        for r in rows:
            d = dict(zip(cols, r))
            d["upserted_asins"] = _parse_json_list(d.get("upserted_asins"))
            d["soft_deleted_asins"] = _parse_json_list(d.get("soft_deleted_asins"))
            out.append(d)

        return out, total


# --- Restore helpers -------------------------------------------------

async def get_all_asins_async(db_path: Path, include_deleted: bool = True) -> set[str]:
    """Return the set of ASINs in the DB (optionally only active)."""
    async with open_db(db_path) as conn:
        if include_deleted:
            cur = await conn.execute("SELECT asin FROM items")
        else:
            cur = await conn.execute("SELECT asin FROM items WHERE is_deleted = 0")
        rows = await cur.fetchall()
        await cur.close()
    return {r[0] for r in rows}


async def soft_delete_bulk_async(db_path: Path, asins: set[str]) -> int:
    """Soft-delete a set of ASINs. Returns count affected."""
    if not asins:
        return 0
    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            ts = now_iso_utc()
            for a in asins:
                await conn.execute(
                    "UPDATE items SET is_deleted=1, deleted_utc=?, updated_utc=? WHERE asin=?",
                    (ts, ts, a),
                )
            await conn.commit()
            return len(asins)


async def restore_from_export_async(
    db_path: Path,
    export_payload: dict,
    *,
    replace: bool = False,
    note: Optional[str] = "restore-from-export",
    state_token: Optional[str | int | float] = None,
) -> tuple[int, int]:
    """
    Restore from an exported library JSON.
    """
    if "items" not in export_payload or "response_groups" not in export_payload:
        raise ValueError("export_payload must contain 'items' and 'response_groups'.")

    rg_raw = export_payload.get("response_groups", [])
    if isinstance(rg_raw, list):
        response_groups = ",".join([str(x).strip() for x in rg_raw if str(x).strip()])
    else:
        response_groups = str(rg_raw or "").strip()

    await ensure_initialized_async(db_path, response_groups=response_groups)

    eff_token = state_token if state_token is not None else export_payload.get("state_token")

    if eff_token is not None:
        iso, raw = epoch_ms_to_iso(eff_token)
        async with open_db(db_path) as conn:
            await conn.execute(
                """
                UPDATE settings
                   SET last_state_token_utc = ?,
                       last_state_token_raw = ?
                 WHERE id = 1
                """,
                (iso, raw),
            )
            await conn.commit()

    upserted = await full_import_async(
        db_path,
        export_payload,
        response_token=None,
        note=note,
    )

    deleted = 0
    if replace:
        current_asins = await get_all_asins_async(db_path, include_deleted=False)
        exported_asins = {
            str((it or {}).get("asin"))
            for it in export_payload.get("items", [])
            if (it or {}).get("asin")
        }
        to_soft_delete = current_asins - exported_asins
        deleted = await soft_delete_bulk_async(db_path, to_soft_delete)

    return upserted, deleted
