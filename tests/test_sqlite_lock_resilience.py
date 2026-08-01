from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import IS_SQLITE, SessionLocal, engine, init_db
from app.log_service import write_log

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not IS_SQLITE, reason="SQLite-specific local runtime test")
def test_sqlite_runtime_enables_wal_and_busy_timeout() -> None:
    init_db()
    with engine.connect() as connection:
        journal_mode = str(connection.execute(text("PRAGMA journal_mode")).scalar_one()).lower()
        busy_timeout = int(connection.execute(text("PRAGMA busy_timeout")).scalar_one())

    assert journal_mode == "wal"
    assert busy_timeout >= 30_000


@pytest.mark.skipif(not IS_SQLITE, reason="SQLite-specific local runtime test")
def test_write_log_does_not_leave_a_read_transaction_open() -> None:
    init_db()
    with SessionLocal() as db:
        row = write_log(
            db,
            category="test.sqlite.transaction",
            message="transaction boundary regression test",
        )
        assert row.id is not None
        assert db.in_transaction() is False


def test_bridge_command_releases_database_before_websocket_wait() -> None:
    source = (ROOT / "app/api/bridges.py").read_text(encoding="utf-8")
    start = source.index("async def send_command")
    rollback = source.index("db.rollback()", start)
    websocket_wait = source.index("await bridge_hub.send_command", start)

    assert rollback < websocket_wait
    assert "with SessionLocal() as log_db" in source


def test_console_bootstrap_hides_legacy_ui_and_bounds_requests() -> None:
    loader = (ROOT / "app/static/gpt_in_speech_patch.js").read_text(encoding="utf-8")
    timeout_patch = (ROOT / "app/static/request_timeout_patch.js").read_text(encoding="utf-8")
    doctor = (ROOT / "app/static/dsp_doctor_feedback_patch.js").read_text(encoding="utf-8")

    assert loader.index("request_timeout_patch.js") < loader.index("console_shell_v3.js")
    assert "aliver-shell-booting" in loader
    assert "AbortController" in timeout_patch
    assert "请求超过" in timeout_patch
    assert "最长约 53 秒" in doctor
    assert "database is locked" in doctor
