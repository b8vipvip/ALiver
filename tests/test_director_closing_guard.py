from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import pro_director_service
from app.json_utils import dumps, loads


def closing_run(*, sent: bool = True):
    return SimpleNamespace(
        state_json=dumps({"closing_sent": sent}),
        status="closing" if sent else "live",
        phase="closing" if sent else "topic",
        current_segment_index=0,
        current_segment_started_at=None,
        rundown_json=dumps(
            [
                {"id": "topic", "name": "主题", "duration_seconds": 60},
                {"id": "closing", "name": "收尾", "duration_seconds": 60},
            ]
        ),
        last_decision_at=None,
        next_cue_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_ai_director_holds_after_closing_was_sent():
    result = await pro_director_service.ai_director_decision(None, closing_run(), {}, [])

    assert result["decision_type"] == "hold"
    assert result["instruction"] == ""
    assert "不再重复" in result["reason"]


def test_close_decision_moves_run_to_terminal_closing_state():
    run = closing_run(sent=False)

    pro_director_service.apply_decision_state(
        run,
        {},
        {
            "decision_type": "close",
            "instruction": "自然结束直播。",
            "reason": "计划时长结束",
            "next_cue_seconds": 30,
        },
        None,
    )

    assert run.status == "closing"
    assert run.phase == "closing"
    assert run.current_segment_index == 1
    assert run.next_cue_at is None
    assert loads(run.state_json, {})["closing_sent"] is True


def test_repeated_manual_close_is_idempotent(monkeypatch):
    run = closing_run()

    class FakeDb:
        committed = 0
        refreshed = 0

        def commit(self):
            self.committed += 1

        def refresh(self, value):
            assert value is run
            self.refreshed += 1

    db = FakeDb()
    monkeypatch.setattr(pro_director_service, "get_or_create_run", lambda *_args, **_kwargs: run)

    result = pro_director_service.control_run(db, None, {}, "close")

    assert result is run
    assert db.committed == 1
    assert db.refreshed == 1
    assert run.next_cue_at is None
    assert loads(run.state_json, {})["closing_sent"] is True
