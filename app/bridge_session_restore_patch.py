from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app import session_reconciliation
from app.db import SessionLocal
from app.json_utils import dumps, loads
from app.log_service import write_log
from app.models import AvatarSession, ProviderConfig

_RESTORABLE_CODES = {
    "bridge_session_missing",
    "bridge_session_failed",
    "bridge_session_ended",
    "bridge_session_tasks_stopped",
}
_ACTIVE_STATUSES = {"starting", "active", "running", "ready", "awaiting_manual", "reconnecting"}
_inflight: set[str] = set()
_last_attempt: dict[str, float] = {}


async def _restore_latest_session(bridge_id: str, session_ids: list[str]) -> None:
    if bridge_id in _inflight:
        return
    now = time.monotonic()
    if now - _last_attempt.get(bridge_id, 0.0) < 20.0:
        return
    _last_attempt[bridge_id] = now
    _inflight.add(bridge_id)
    try:
        await asyncio.sleep(0.8)
        with SessionLocal() as db:
            active = db.scalar(
                select(AvatarSession).where(
                    AvatarSession.bridge_id == bridge_id,
                    AvatarSession.status.in_(_ACTIVE_STATUSES),
                )
            )
            if active is not None:
                return
            candidates = db.scalars(
                select(AvatarSession)
                .where(
                    AvatarSession.bridge_id == bridge_id,
                    AvatarSession.id.in_(session_ids),
                    AvatarSession.status == "interrupted",
                )
                .order_by(AvatarSession.updated_at.desc(), AvatarSession.created_at.desc())
            ).all()
            row = candidates[0] if candidates else None
            if row is None:
                return
            config = db.get(ProviderConfig, row.provider_config_id)
            if config is None or not config.enabled:
                return

            response = loads(row.response_json, {})
            if not isinstance(response, dict):
                response = {}
            restore_info = response.get("auto_restore")
            if isinstance(restore_info, dict) and restore_info.get("completed_at"):
                return
            response["auto_restore"] = {
                "trigger": "bridge_reconnected",
                "bridge_id": bridge_id,
                "attempted_at": datetime.now(timezone.utc).isoformat(),
            }
            row.response_json = dumps(response)
            row.status = "starting"
            row.external_session_id = None
            row.error_message = None
            row.started_at = None
            row.ended_at = None
            db.commit()

            from app.api.sessions import row_overrides, row_session_name, start_existing_row

            result, bridge, bridge_result = await start_existing_row(
                row,
                config,
                row_overrides(row),
                db,
            )
            response = loads(row.response_json, {})
            if not isinstance(response, dict):
                response = {}
            response["auto_restore"] = {
                "trigger": "bridge_reconnected",
                "bridge_id": bridge_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "status": row.status,
                "success": row.status in _ACTIVE_STATUSES,
            }
            row.response_json = dumps(response)
            db.commit()
            write_log(
                db,
                category="session.auto_restored",
                level="INFO" if row.status in _ACTIVE_STATUSES else "ERROR",
                message=(
                    f"Bridge 上线后已自动恢复数字人会话：{row_session_name(row, config.name)}"
                    if row.status in _ACTIVE_STATUSES
                    else f"Bridge 上线后自动恢复数字人会话失败：{row_session_name(row, config.name)}"
                ),
                provider_id=config.id,
                session_id=row.id,
                bridge_id=bridge_id,
                details={
                    "status": row.status,
                    "error": row.error_message,
                    "provider_success": result.success,
                    "bridge_name": bridge.name if bridge else None,
                    "bridge_result": bridge_result,
                },
            )
    except Exception as exc:
        with SessionLocal() as db:
            write_log(
                db,
                category="session.auto_restore.failed",
                level="ERROR",
                message=f"Bridge 上线后自动恢复会话异常：{type(exc).__name__}: {exc}",
                bridge_id=bridge_id,
                details={"session_ids": session_ids},
            )
    finally:
        _inflight.discard(bridge_id)


def install_bridge_session_restore_patch() -> None:
    if getattr(session_reconciliation, "_aliver_session_restore_patch", False):
        return
    original = session_reconciliation.reconcile_bridge_sessions

    def reconcile_bridge_sessions(db: Any, bridge_id: str, metadata: Any) -> list[dict[str, Any]]:
        changes = original(db, bridge_id, metadata)
        session_ids = [
            str(item.get("session_id") or "")
            for item in changes
            if str(item.get("code") or "") in _RESTORABLE_CODES and item.get("session_id")
        ]
        if session_ids:
            try:
                asyncio.get_running_loop().create_task(
                    _restore_latest_session(bridge_id, session_ids),
                    name=f"aliver-session-auto-restore-{bridge_id[:8]}",
                )
            except RuntimeError:
                pass
        return changes

    session_reconciliation.reconcile_bridge_sessions = reconcile_bridge_sessions
    session_reconciliation._aliver_session_restore_patch = True
