from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings


def _build_engine() -> Engine:
    url = settings.database_url
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        # sqlite:///data/webui.db -> data/webui.db
        db_path = url.split("///", 1)[1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
