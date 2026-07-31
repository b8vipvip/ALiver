from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app import auto_director_service as service
from app import pro_director_service as director
from app.json_utils import loads
from app.models import AudienceEvent, AutoDirectorRun

_ORIGINAL_PROCESS_CONFIG: Any = None


def _is_preflight_simulation(event: AudienceEvent | None) -> bool:
    if event is None:
        return False
    payload = loads(event.payload_json, {})
    visible = payload.get("visible_collector") if isinstance(payload, dict) else None
    if not isinstance(visible, dict):
        return False
    return bool(visible.get("simulated")) and str(visible.get("validation_phase") or "") == "preflight"


def _queued_preflight_event(db: Any, config_id: str) -> AudienceEvent | None:
    rows = db.scalars(
        select(AudienceEvent)
        .where(
            AudienceEvent.config_id == config_id,
            AudienceEvent.status == "queued",
        )
        .order_by(AudienceEvent.created_at.desc())
        .limit(20)
    ).all()
    return next((row for row in rows if _is_preflight_simulation(row)), None)


def _snapshot_run(run: AutoDirectorRun) -> dict[str, Any]:
    fields = (
        "status",
        "phase",
        "current_segment_index",
        "rundown_json",
        "state_json",
        "started_at",
        "paused_at",
        "ended_at",
        "current_segment_started_at",
        "last_decision_at",
        "next_cue_at",
    )
    return {field: getattr(run, field) for field in fields}


def _restore_run(db: Any, run_id: str, snapshot: dict[str, Any]) -> None:
    run = db.get(AutoDirectorRun, run_id)
    if run is None:
        return
    for field, value in snapshot.items():
        setattr(run, field, value)
    db.commit()
    db.refresh(run)


async def _patched_process_config(
    db: Any,
    config: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if not force:
        return await _ORIGINAL_PROCESS_CONFIG(db, config, force=force)

    settings = service.merged_settings(config)
    if not bool(settings.get("professional_mode", True)):
        return await _ORIGINAL_PROCESS_CONFIG(db, config, force=force)

    event = _queued_preflight_event(db, config.id)
    if event is None:
        return await _ORIGINAL_PROCESS_CONFIG(db, config, force=force)

    run = director.get_or_create_run(db, config, settings)
    if run.status in director.RUNNING_STATUSES:
        return await _ORIGINAL_PROCESS_CONFIG(db, config, force=force)

    snapshot = _snapshot_run(run)
    original_status = str(run.status or "stopped")
    try:
        if run.status == "emergency":
            director.control_run(db, config, settings, "reset")
        elif run.status == "paused":
            director.control_run(db, config, settings, "reset")
        director.control_run(db, config, settings, "start")
        result = dict(await _ORIGINAL_PROCESS_CONFIG(db, config, force=True))
        result.update(
            {
                "validation_run_recovered": True,
                "validation_original_run_status": original_status,
                "validation_run_restored": True,
            }
        )
        return result
    finally:
        _restore_run(db, run.id, snapshot)


def install_live_debug_director_recovery_patch() -> None:
    global _ORIGINAL_PROCESS_CONFIG
    if getattr(service, "_aliver_live_debug_director_recovery_v1", False):
        return
    _ORIGINAL_PROCESS_CONFIG = service.process_config
    service.process_config = _patched_process_config
    service._aliver_live_debug_director_recovery_v1 = True
