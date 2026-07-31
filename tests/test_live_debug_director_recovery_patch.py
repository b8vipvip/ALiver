from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import live_debug_director_recovery_patch as recovery


def test_preflight_simulation_recovers_and_restores_director(monkeypatch):
    run = SimpleNamespace(
        id="run-1",
        status="emergency",
        phase="emergency",
        current_segment_index=0,
        rundown_json="[]",
        state_json="{}",
        started_at=None,
        paused_at=None,
        ended_at=None,
        current_segment_started_at=None,
        last_decision_at=None,
        next_cue_at=None,
    )
    event = SimpleNamespace(id="event-1")
    config = SimpleNamespace(id="config-1")
    actions = []
    restored = []

    monkeypatch.setattr(recovery.service, "merged_settings", lambda _config: {"professional_mode": True})
    monkeypatch.setattr(recovery, "_queued_preflight_event", lambda _db, _config_id: event)
    monkeypatch.setattr(recovery.director, "get_or_create_run", lambda _db, _config, _settings: run)

    def control(_db, _config, _settings, action):
        actions.append(action)
        if action == "reset":
            run.status = "stopped"
            run.phase = "standby"
        elif action == "start":
            run.status = "live"
            run.phase = "opening"
        return run

    monkeypatch.setattr(recovery.director, "control_run", control)
    monkeypatch.setattr(
        recovery,
        "_restore_run",
        lambda _db, run_id, snapshot: restored.append((run_id, snapshot["status"])),
    )

    async def original(_db, _config, *, force=False):
        assert force is True
        assert run.status == "live"
        return {"processed": True, "action": "dispatched", "command_id": "command-1"}

    monkeypatch.setattr(recovery, "_ORIGINAL_PROCESS_CONFIG", original)
    result = asyncio.run(recovery._patched_process_config(SimpleNamespace(), config, force=True))

    assert actions == ["reset", "start"]
    assert result["processed"] is True
    assert result["validation_run_recovered"] is True
    assert result["validation_original_run_status"] == "emergency"
    assert restored == [("run-1", "emergency")]
