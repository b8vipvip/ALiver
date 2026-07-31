from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

if settings.database_url.startswith("sqlite:///"):
    db_path = settings.database_url.removeprefix("sqlite:///")
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _apply_compatibility_migrations() -> None:
    """Apply tiny additive migrations for local SQLite installs without Alembic."""

    if not settings.database_url.startswith("sqlite"):
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

    Base.metadata.create_all(bind=engine)
    _apply_compatibility_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
