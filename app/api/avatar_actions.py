from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.avatar_action_service import active_vtube_session, route_active_avatar_action
from app.bridge_hub import bridge_hub
from app.db import get_db

router = APIRouter(
    prefix="/api/avatar-actions",
    tags=["avatar-actions"],
    dependencies=[Depends(require_admin_token)],
)


class AvatarActionRouteRequest(BaseModel):
    action: str = Field(pattern="^(idle|talking|thinking|wave|happy|surprised|reset)$")
    source: str = Field(default="manual.director", min_length=1, max_length=120)
    priority: int | None = Field(default=None, ge=0, le=100)
    duration_ms: int | None = Field(default=None, ge=0, le=120_000)
    interrupt: bool = True
    force: bool = False
    correlation_id: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AvatarActionClearRequest(BaseModel):
    source: str | None = Field(default=None, max_length=120)
    include_active: bool = True


@router.get("/status")
async def avatar_action_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    active = active_vtube_session(db)
    if active is None:
        return {"active_session": None, "status": None}
    session, provider = active
    if not session.bridge_id or not bridge_hub.is_connected(session.bridge_id):
        return {
            "active_session": {
                "id": session.id,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "bridge_id": session.bridge_id,
            },
            "status": None,
            "reason": "bridge_offline",
        }
    try:
        result = await bridge_hub.send_command(
            session.bridge_id,
            "provider.vtube_studio.action.router_status",
            {"session_id": session.id},
            4.0,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "active_session": {
            "id": session.id,
            "provider_id": provider.id,
            "provider_name": provider.name,
            "bridge_id": session.bridge_id,
        },
        "status": result,
    }


@router.post("/route")
async def route_avatar_action(
    payload: AvatarActionRouteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await route_active_avatar_action(
            db,
            payload.action,
            source=payload.source,
            priority=payload.priority,
            duration_ms=payload.duration_ms,
            interrupt=payload.interrupt,
            force=payload.force,
            correlation_id=payload.correlation_id,
            metadata=payload.metadata,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/clear")
async def clear_avatar_actions(
    payload: AvatarActionClearRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    active = active_vtube_session(db)
    if active is None:
        return {"cleared": False, "reason": "no_active_vtube_session"}
    session, _provider = active
    if not session.bridge_id or not bridge_hub.is_connected(session.bridge_id):
        raise HTTPException(status_code=409, detail="当前 VTube Studio 会话的 Bridge 未连接")
    try:
        result = await bridge_hub.send_command(
            session.bridge_id,
            "provider.vtube_studio.action.queue_clear",
            {
                "session_id": session.id,
                "source": payload.source,
                "include_active": payload.include_active,
            },
            4.0,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cleared": True, "result": result}
