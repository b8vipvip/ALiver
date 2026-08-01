from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
IS_SQLITE = settings.database_url.startswith("sqlite")

if settings.database_url.startswith("sqlite:///"):
    db_path = settings.database_url.removeprefix("sqlite:///")
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

connect_args = (
    {"check_same_thread": False, "timeout": 30.0}
    if IS_SQLITE
    else {}
)
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    future=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
        """Make every pooled SQLite connection tolerate short write contention."""

        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _enable_sqlite_wal() -> None:
    """Enable concurrent readers and one writer for the local control database."""

    if not IS_SQLITE:
        return
    connection = engine.raw_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.fetchone()
        cursor.execute("PRAGMA synchronous=NORMAL")
        connection.commit()
    finally:
        cursor.close()
        connection.close()


def _apply_compatibility_migrations() -> None:
    """Apply tiny additive migrations for local SQLite installs without Alembic."""

    if not IS_SQLITE:
        return
    inspector = inspect(engine)
    if "voice_profiles" not in inspector.get_table_names():
        return
    columns = {row["name"] for row in inspector.get_columns("voice_profiles")}
    if "native_tuning_json" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE voice_profiles ADD COLUMN native_tuning_json TEXT NOT NULL DEFAULT '{}'")
            )


def init_db() -> None:
    from app import (
        models,  # noqa: F401
        voice_models,  # noqa: F401
    )

    _enable_sqlite_wal()
    Base.metadata.create_all(bind=engine)
    _apply_compatibility_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    finally:
        if db.in_transaction():
            db.rollback()
        db.close()
