from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app import pro_director_service

_original_base_context = pro_director_service._base_context
_original_ai_director_decision = pro_director_service.ai_director_decision
_original_apply_decision_state = pro_director_service.apply_decision_state
_original_control_run = pro_director_service.control_run


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def safe_base_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return the AI director context as plain JSON-compatible data."""
    context = _original_base_context(*args, **kwargs)
    return json.loads(json.dumps(context, ensure_ascii=False, default=_json_default))


def _closing_hold(reason: str = "收尾口播已经发送，保持关闭状态，不再重复下发") -> dict[str, Any]:
    return {
        "decision_type": "hold",
        "event_id": None,
        "instruction": "",
        "avatar_action": None,
        "priority": 0,
        "duration_seconds": 0,
        "reason": reason,
        "topic": None,
        "next_cue_seconds": 600,
    }


async def guarded_ai_director_decision(
    config: Any,
    run: Any,
    settings: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Treat a dispatched closing instruction as a one-shot terminal cue."""
    if bool(pro_director_service.run_state(run).get("closing_sent")):
        return _closing_hold()
    return await _original_ai_director_decision(config, run, settings, candidates)


def guarded_apply_decision_state(
    run: Any,
    settings: dict[str, Any],
    decision: dict[str, Any],
    event: Any,
) -> None:
    """Move a close decision into a stable closing state with no future cue timer."""
    _original_apply_decision_state(run, settings, decision, event)
    if str(decision.get("decision_type") or "hold") != "close":
        return

    rundown = pro_director_service.current_rundown(run, settings)
    closing_index = next(
        (index for index, item in enumerate(rundown) if str(item.get("id")) == "closing"),
        max(0, len(rundown) - 1),
    )
    run.status = "closing"
    run.phase = "closing"
    run.current_segment_index = closing_index
    run.current_segment_started_at = pro_director_service.utcnow()
    run.next_cue_at = None

    state = pro_director_service.run_state(run)
    state["closing_sent"] = True
    state["last_reason"] = str(decision.get("reason") or "收尾口播已经发送")
    run.state_json = pro_director_service.dumps(state)


def guarded_control_run(db: Any, config: Any, settings: dict[str, Any], action: str) -> Any:
    """Make repeated clicks on '进入收尾' idempotent after the closing cue was sent."""
    if str(action or "").strip().lower() == "close":
        run = pro_director_service.get_or_create_run(db, config, settings)
        state = pro_director_service.run_state(run)
        if run.status == "closing" and bool(state.get("closing_sent")):
            state["last_reason"] = "收尾口播已经发送，保持关闭状态，不重复下发"
            run.state_json = pro_director_service.dumps(state)
            run.next_cue_at = None
            db.commit()
            db.refresh(run)
            return run
    return _original_control_run(db, config, settings, action)


pro_director_service._base_context = safe_base_context
pro_director_service.ai_director_decision = guarded_ai_director_decision
pro_director_service.apply_decision_state = guarded_apply_decision_state
pro_director_service.control_run = guarded_control_run

# Install after the closing guard so the welcome patch composes with the existing
# one-shot closing protection and is what auto_director_service imports.
from app.live_welcome_patch import install_live_welcome_patch  # noqa: E402

install_live_welcome_patch()

# Import and patch the event scorer before douyin_live_service imports score_event
# by value. This guarantees viewer-entry events reach the director even when the
# configured minimum score is higher than the old generic system-event score.
from app.live_welcome_ingest_patch import install_live_welcome_ingest_patch  # noqa: E402

install_live_welcome_ingest_patch()

# A preflight simulation must be able to validate the welcome pipeline without
# permanently changing an emergency/stopped/paused production director run.
from app.live_debug_director_recovery_patch import (  # noqa: E402
    install_live_debug_director_recovery_patch,
)

install_live_debug_director_recovery_patch()
