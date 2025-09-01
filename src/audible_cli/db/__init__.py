from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import aiosqlite
from filelock import FileLock

SchemaFn = Callable[[aiosqlite.Connection], Awaitable[None]]


# ---------------- Registry ----------------

@dataclass(frozen=True)
class _SchemaEntry:
    name: str
    fn: SchemaFn
    order: int


_REGISTRY: list[_SchemaEntry] = []


def register_schema(name: str, fn: SchemaFn, *, order: int = 100) -> None:
    """Register a schema ensure-function to be executed during init.

    Args:
        name: Unique schema name (e.g., "library", "assets").
        fn: Async callable(conn) -> None, idempotent schema creation/migration.
        order: Execution order (lower runs earlier). Keep stable across versions.
    """
    if any(e.name == name for e in _REGISTRY):
        return
    _REGISTRY.append(_SchemaEntry(name=name, fn=fn, order=order))


async def ensure_all_schemas(conn: aiosqlite.Connection) -> None:
    """Run all registered schema ensure-functions in deterministic order.

    Args:
        conn: Open aiosqlite connection.

    Returns:
        None
    """
    for entry in sorted(_REGISTRY, key=lambda e: (e.order, e.name)):
        await entry.fn(conn)


# ---------------- Locking / DB utils ----------------

class AsyncFileLock:
    """Async wrapper around filelock.FileLock to keep the loop non-blocking."""

    def __init__(self, path: Path, timeout: float = 30.0):
        self._lock = FileLock(str(path), timeout=timeout)

    async def __aenter__(self):
        await asyncio.to_thread(self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await asyncio.to_thread(self._lock.release)


@asynccontextmanager
async def open_db(db_path: Path):
    """Open aiosqlite connection with sensible PRAGMA defaults.

    Args:
        db_path: SQLite database file path.

    Yields:
        An open aiosqlite.Connection with sane pragmas.
    """
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    await conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        yield conn
    finally:
        await conn.close()


def now_iso_utc() -> str:
    """Return current UTC timestamp (ISO-8601, seconds precision, 'Z').

    Returns:
        ISO-8601 UTC string.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- Public high-level helpers ----------------

async def ensure_db_ready_async(db_path: Path) -> None:
    """Create the database and ensure all registered schemas.

    Args:
        db_path: SQLite database file path.

    Returns:
        None
    """
    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_all_schemas(conn)
            await conn.commit()


async def with_txn_async(
    db_path: Path,
    work: Callable[[aiosqlite.Connection], Awaitable[object]],
) -> object:
    """Run `work(conn)` inside a managed IMMEDIATE transaction with file lock.

    Args:
        db_path: SQLite database file path.
        work: Async callable receiving the open connection.

    Returns:
        The result of `work`.
    """
    lock_path = db_path.with_suffix(".lock")
    async with AsyncFileLock(lock_path):
        async with open_db(db_path) as conn:
            await ensure_all_schemas(conn)
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                result = await work(conn)
                await conn.commit()
                return result
            except Exception:
                await conn.rollback()
                raise
