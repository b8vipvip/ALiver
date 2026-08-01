from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bridge_hub import bridge_hub
from app.json_utils import loads
from app.models import AvatarSession, DirectorCommand, ProviderConfig

ACTIVE_SESSION_STATUSES = {
    "starting",
    "active",
    "running",
    "ready",
    "awaiting_manual",
    "reconnecting",
}
VTUBE_PROVIDER_TYPES = {"vtube_studio", "local_vtube_studio"}
AVATAR_ACTIONS = {"idle", "talking", "thinking", "wave", "happy", "surprised", "reset"}

_extension_generating_state: dict[str, bool] = {}
_background_tasks: set[asyncio.Task] = set()


def active_vtube_session(db: Session) -> tuple[AvatarSession, ProviderConfig] | None:
    row = db.execute(
        select(AvatarSession, ProviderConfig)
        .join(ProviderConfig, ProviderConfig.id == AvatarSession.provider_config_id)
        .where(
            AvatarSession.status.in_(ACTIVE_SESSION_STATUSES),
            ProviderConfig.provider_type.in_(VTUBE_PROVIDER_TYPES),
        )
        .order_by(AvatarSession.started_at.desc(), AvatarSession.created_at.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    return row[0], row[1]


async def route_active_avatar_action(
    db: Session,
    action: str,
    *,
    source: str,
    priority: int | None = None,
    duration_ms: int | None = None,
    interrupt: bool = True,
    force: bool = False,
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    timeout: float = 4.0,
) -> dict[str, Any]:
    normalized = str(action or "").strip().lower()
    if normalized not in AVATAR_ACTIONS:
        raise ValueError(f"Unsupported avatar action: {normalized or 'empty'}")
    active = active_vtube_session(db)
    if active is None:
        return {"routed": False, "reason": "no_active_vtube_session"}
    session, provider = active
    if not session.bridge_id or not bridge_hub.is_connected(session.bridge_id):
        return {
            "routed": False,
            "reason": "bridge_offline",
            "session_id": session.id,
            "bridge_id": session.bridge_id,
        }
    payload: dict[str, Any] = {
        "session_id": session.id,
        "action": normalized,
        "source": str(source or "system")[:120],
        "interrupt": bool(interrupt),
        "force": bool(force),
        "metadata": dict(metadata or {}),
    }
    if priority is not None:
        payload["priority"] = max(0, min(int(priority), 100))
    if duration_ms is not None:
        payload["duration_ms"] = max(0, min(int(duration_ms), 120_000))
    if correlation_id:
        payload["correlation_id"] = str(correlation_id)[:160]
    result = await bridge_hub.send_command(
        session.bridge_id,
        "provider.vtube_studio.action.route",
        payload,
        timeout,
    )
    return {
        "routed": bool(result.get("ok", True)),
        "session_id": session.id,
        "provider_id": provider.id,
        "bridge_id": session.bridge_id,
        "result": result,
    }


def schedule_active_avatar_action(
    action: str,
    *,
    source: str,
    priority: int | None = None,
    duration_ms: int | None = None,
    interrupt: bool = True,
    force: bool = False,
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    from app.db import SessionLocal

    async def run() -> None:
        try:
            with SessionLocal() as db:
                await route_active_avatar_action(
                    db,
                    action,
                    source=source,
                    priority=priority,
                    duration_ms=duration_ms,
                    interrupt=interrupt,
                    force=force,
                    correlation_id=correlation_id,
                    metadata=metadata,
                )
        except Exception:
            # Avatar motion must never block director delivery or extension heartbeats.
            return

    try:
        task = asyncio.create_task(run(), name=f"avatar-action-{source}-{action}")
    except RuntimeError:
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def director_action_for_command(row: DirectorCommand) -> tuple[str, int, int]:
    payload = loads(row.payload_json, {})
    explicit = str(payload.get("avatar_action") or "").strip().lower()
    if explicit in AVATAR_ACTIONS:
        return (
            explicit,
            int(payload.get("avatar_action_priority") or max(70, row.priority)),
            int(payload.get("avatar_action_duration_ms") or 3200),
        )

    source = str(payload.get("source") or "").strip().lower()
    text = f"{payload.get('content', '')} {payload.get('text', '')}".casefold()
    if source == "auto_director":
        if any(word in text for word in ("送出了礼物", "礼物", "感谢", "谢谢")):
            return "happy", max(78, row.priority), 3000
        if any(word in text for word in ("关注了直播间", "欢迎", "打招呼", "新观众")):
            return "wave", max(80, row.priority), 2800
        if any(word in text for word in ("震惊", "惊讶", "没想到", "意外")):
            return "surprised", max(78, row.priority), 1900
    return "thinking", max(64, min(row.priority, 82)), 8000


def schedule_director_command_action(row: DirectorCommand) -> None:
    if row.command_type == "plan_generate":
        return
    action, priority, duration_ms = director_action_for_command(row)
    payload = loads(row.payload_json, {})
    explicit = bool(payload.get("avatar_action"))
    auto_source = str(payload.get("source") or "") == "auto_director"
    if explicit:
        source = "director.explicit"
    elif auto_source:
        source = "auto_director"
    else:
        source = "director.command"
    schedule_active_avatar_action(
        action,
        source=source,
        priority=priority,
        duration_ms=duration_ms,
        interrupt=True,
        correlation_id=row.id,
        metadata={
            "command_id": row.id,
            "command_type": row.command_type,
            "extension_id": row.extension_id,
            "director_source": payload.get("source"),
        },
    )


def schedule_chatgpt_status(extension_id: str, *, generating: bool) -> None:
    previous = _extension_generating_state.get(extension_id)
    current = bool(generating)
    if previous is current:
        return
    _extension_generating_state[extension_id] = current

    # Browser planning is a configuration task, not a live performance. Do not
    # animate the avatar merely because ChatGPT is generating a plan JSON.
    from app.browser_director_plan_service import is_browser_plan_active

    if is_browser_plan_active(extension_id):
        return
    if not current:
        # Speech detection or the action timeout restores the correct live base state.
        # Do not let a low-priority idle signal interrupt a gift/welcome/manual action.
        return
    schedule_active_avatar_action(
        "thinking",
        source="chatgpt.generating",
        priority=64,
        duration_ms=12_000,
        interrupt=True,
        correlation_id=extension_id,
        metadata={"extension_id": extension_id, "generating": True},
    )
