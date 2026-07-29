from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.json_utils import dumps, loads
from app.models import AvatarSession

ACTIVE_SESSION_STATUSES = {"active", "running", "ready", "awaiting_manual"}


def classify_local_session(local_state: Any) -> tuple[str, str] | None:
    if not isinstance(local_state, dict):
        return (
            "bridge_session_missing",
            "Bridge 已重新连接，但本地不存在该数字人会话。旧会话可能在 Bridge 重启或异常退出时中断。",
        )

    status = str(local_state.get("status") or "unknown").lower()
    renderer_done = bool(local_state.get("renderer_task_done"))
    sender_done = bool(local_state.get("sender_task_done"))
    error = str(local_state.get("error") or "").strip()

    if status == "failed":
        return (
            "bridge_session_failed",
            error or "Bridge 报告本地数字人会话已经失败。",
        )
    if status in {"ended", "stopped", "closed", "ended_unexpected"}:
        return (
            "bridge_session_ended",
            "Bridge 报告本地数字人会话已经结束。",
        )
    if status in {"active", "running", "starting"} and (renderer_done or sender_done):
        ended_tasks = []
        if renderer_done:
            ended_tasks.append("数字人音视频渲染任务")
        if sender_done:
            ended_tasks.append("GPT_OUT 音频发送任务")
        return (
            "bridge_session_tasks_stopped",
            "、".join(ended_tasks) + "已经停止，但旧状态仍显示为运行中。",
        )
    return None


def _local_sessions(metadata: dict[str, Any]) -> dict[str, Any] | None:
    generic = metadata.get("avatar_sessions")
    if isinstance(generic, dict):
        return generic

    # Backward compatibility with Bridge versions that only reported Simli sessions.
    simli = metadata.get("simli_sessions")
    vtube = metadata.get("vtube_studio_sessions")
    if isinstance(simli, dict) or isinstance(vtube, dict):
        merged: dict[str, Any] = {}
        if isinstance(simli, dict):
            merged.update(simli)
        if isinstance(vtube, dict):
            merged.update(vtube)
        return merged
    return None


def reconcile_bridge_sessions(
    db: Session,
    bridge_id: str,
    metadata: Any,
) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []
    local_sessions = _local_sessions(metadata)
    if local_sessions is None:
        return []

    rows = db.scalars(
        select(AvatarSession).where(
            AvatarSession.bridge_id == bridge_id,
            AvatarSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    ).all()
    now = datetime.now(timezone.utc)
    changes: list[dict[str, Any]] = []
    for row in rows:
        local_state = local_sessions.get(row.id)
        finding = classify_local_session(local_state)
        if finding is None:
            continue
        code, message = finding
        previous_status = row.status
        row.status = "interrupted"
        row.ended_at = now
        row.error_message = message
        response = loads(row.response_json, {})
        if not isinstance(response, dict):
            response = {}
        response["bridge_reconciliation"] = {
            "code": code,
            "message_zh": message,
            "previous_status": previous_status,
            "local_state": local_state,
            "detected_at": now.isoformat(),
        }
        row.response_json = dumps(response)
        changes.append(
            {
                "session_id": row.id,
                "previous_status": previous_status,
                "status": row.status,
                "code": code,
                "message_zh": message,
                "local_state": local_state,
            }
        )
    if changes:
        db.commit()
    return changes
