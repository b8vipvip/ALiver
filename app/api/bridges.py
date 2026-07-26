from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import bridge_to_out
from app.auth import require_admin_token
from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import get_db
from app.json_utils import dumps
from app.log_service import write_log
from app.models import BridgeAgent
from app.schemas import (
    BridgeCommandRequest,
    BridgeHeartbeat,
    BridgeOut,
    BridgeRegister,
    BridgeRegisterOut,
)
from app.security import generate_token, hash_token, verify_token

router = APIRouter(prefix="/api/bridges", tags=["bridges"])


def _bridge_auth(
    bridge_id: str,
    token: str | None,
    db: Session,
) -> BridgeAgent:
    row = db.get(BridgeAgent, bridge_id)
    if not row or not token or not verify_token(token, row.token_hash):
        raise HTTPException(status_code=401, detail="Invalid bridge credentials")
    return row


@router.post("/register", response_model=BridgeRegisterOut, status_code=status.HTTP_201_CREATED)
def register_bridge(payload: BridgeRegister, db: Session = Depends(get_db)) -> BridgeRegisterOut:
    token = generate_token()
    row = BridgeAgent(
        name=payload.name,
        machine_name=payload.machine_name,
        version=payload.version,
        capabilities_json=dumps(payload.capabilities),
        metadata_json=dumps(payload.metadata),
        token_hash=hash_token(token),
        status="offline",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    write_log(
        db,
        category="bridge.registered",
        message=f"Bridge registered: {row.name}",
        bridge_id=row.id,
        details={"machine_name": row.machine_name, "version": row.version},
    )
    return BridgeRegisterOut(bridge_id=row.id, token=token)


@router.post("/{bridge_id}/heartbeat")
def heartbeat(
    bridge_id: str,
    payload: BridgeHeartbeat,
    x_bridge_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    row = _bridge_auth(bridge_id, x_bridge_token, db)
    row.last_seen_at = datetime.now(timezone.utc)
    row.status = "online"
    if payload.version is not None:
        row.version = payload.version
    if payload.capabilities is not None:
        row.capabilities_json = dumps(payload.capabilities)
    if payload.metadata is not None:
        row.metadata_json = dumps(payload.metadata)
    db.commit()
    return {"ok": True, "connected": bridge_hub.is_connected(bridge_id)}


@router.get("", response_model=list[BridgeOut], dependencies=[Depends(require_admin_token)])
def list_bridges(db: Session = Depends(get_db)) -> list[BridgeOut]:
    rows = db.scalars(select(BridgeAgent).order_by(BridgeAgent.created_at.desc())).all()
    return [bridge_to_out(row, bridge_hub.is_connected(row.id)) for row in rows]


@router.post("/{bridge_id}/commands", dependencies=[Depends(require_admin_token)])
async def send_command(
    bridge_id: str,
    payload: BridgeCommandRequest,
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(BridgeAgent, bridge_id)
    if not row:
        raise HTTPException(status_code=404, detail="Bridge not found")
    timeout = payload.timeout_seconds or get_settings().bridge_command_timeout
    try:
        result = await bridge_hub.send_command(
            bridge_id,
            payload.command_type,
            payload.payload,
            timeout,
        )
        write_log(
            db,
            category="bridge.command",
            message=f"Bridge command completed: {payload.command_type}",
            bridge_id=bridge_id,
            details=result,
        )
        return result
    except RuntimeError as exc:
        write_log(
            db,
            category="bridge.command",
            message=f"Bridge command failed: {payload.command_type}",
            level="ERROR",
            bridge_id=bridge_id,
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
