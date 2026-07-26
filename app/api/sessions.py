from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import session_to_out
from app.auth import require_admin_token
from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import get_db
from app.json_utils import dumps, loads
from app.log_service import write_log
from app.models import AvatarSession, BridgeAgent, ProviderConfig
from app.provider_manager import build_provider
from app.schemas import SessionCreate, SessionOut

router = APIRouter(
    prefix="/api/sessions",
    tags=["sessions"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionOut]:
    rows = db.scalars(select(AvatarSession).order_by(AvatarSession.created_at.desc())).all()
    providers = {
        row.id: row
        for row in db.scalars(select(ProviderConfig)).all()
    }
    return [session_to_out(row, providers.get(row.provider_config_id)) for row in rows]


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> SessionOut:
    config = db.get(ProviderConfig, payload.provider_config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")
    if not config.enabled:
        raise HTTPException(status_code=409, detail="Provider is disabled")

    provider = build_provider(config)
    if provider.execution_mode == "bridge":
        if not payload.bridge_id:
            raise HTTPException(status_code=422, detail="This provider requires bridge_id")
        bridge = db.get(BridgeAgent, payload.bridge_id)
        if not bridge:
            raise HTTPException(status_code=404, detail="Bridge not found")
        if not bridge_hub.is_connected(bridge.id):
            raise HTTPException(status_code=409, detail="Bridge is offline")

    row = AvatarSession(
        provider_config_id=config.id,
        bridge_id=payload.bridge_id,
        status="starting",
        request_json=dumps(payload.overrides),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    result = await provider.create_session(payload.overrides)
    if result.success and provider.execution_mode == "bridge":
        command_payload = {
            "session_id": row.id,
            "provider_id": config.id,
            "provider_name": config.name,
            "provider_type": config.provider_type,
            "provider_plan": result.data,
        }
        try:
            bridge_result = await bridge_hub.send_command(
                payload.bridge_id or "",
                "provider.start_session",
                command_payload,
                get_settings().bridge_command_timeout,
            )
            ok = bool(bridge_result.get("ok"))
            bridge_data = bridge_result.get("data") or {}
            row.status = str(bridge_data.get("status") or ("active" if ok else "failed"))
            row.external_session_id = bridge_data.get("external_session_id")
            row.response_json = dumps(bridge_result)
            row.error_message = None if ok else str(bridge_result.get("error") or "Bridge failed")
        except RuntimeError as exc:
            row.status = "failed"
            row.error_message = str(exc)
            row.response_json = dumps({"error": str(exc)})
    elif result.success:
        row.status = str(result.data.get("status") or "active")
        row.external_session_id = result.external_session_id
        row.response_json = dumps(result.data)
    else:
        row.status = "failed"
        row.error_message = result.error
        row.response_json = dumps(result.data)

    if row.status in {"active", "running", "awaiting_manual", "ready"}:
        row.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)

    write_log(
        db,
        category="session.start",
        message=f"Session start {'succeeded' if row.status != 'failed' else 'failed'}",
        level="INFO" if row.status != "failed" else "ERROR",
        provider_id=config.id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details={"status": row.status, "error": row.error_message},
        latency_ms=result.latency_ms,
    )
    return session_to_out(row, config)


@router.post("/{session_id}/stop", response_model=SessionOut)
async def stop_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    row = db.get(AvatarSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    config = db.get(ProviderConfig, row.provider_config_id)
    if not config:
        raise HTTPException(status_code=409, detail="Provider configuration no longer exists")
    provider = build_provider(config)
    response_data = loads(row.response_json, {})

    if provider.execution_mode == "bridge":
        if not row.bridge_id or not bridge_hub.is_connected(row.bridge_id):
            row.status = "ended_local_only"
            row.ended_at = datetime.now(timezone.utc)
            row.error_message = "Bridge offline; session marked ended locally"
            db.commit()
        else:
            plan = await provider.stop_session(row.external_session_id, response_data)
            try:
                bridge_result = await bridge_hub.send_command(
                    row.bridge_id,
                    "provider.stop_session",
                    {
                        "session_id": row.id,
                        "provider_id": config.id,
                        "provider_type": config.provider_type,
                        "provider_plan": plan.data,
                        "external_session_id": row.external_session_id,
                    },
                    get_settings().bridge_command_timeout,
                )
                ok = bool(bridge_result.get("ok"))
                row.status = "ended" if ok else "stop_failed"
                row.response_json = dumps({"start": response_data, "stop": bridge_result})
                row.error_message = None if ok else str(bridge_result.get("error") or "Bridge failed")
                if ok:
                    row.ended_at = datetime.now(timezone.utc)
                db.commit()
            except RuntimeError as exc:
                row.status = "stop_failed"
                row.error_message = str(exc)
                db.commit()
    else:
        result = await provider.stop_session(row.external_session_id, response_data)
        if result.success:
            row.status = "ended"
            row.ended_at = datetime.now(timezone.utc)
            row.error_message = None
            row.response_json = dumps({"start": response_data, "stop": result.data})
        else:
            row.status = "stop_failed"
            row.error_message = result.error
        db.commit()

    db.refresh(row)
    write_log(
        db,
        category="session.stop",
        message=f"Session stop result: {row.status}",
        level="INFO" if row.status in {"ended", "ended_local_only"} else "ERROR",
        provider_id=config.id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details={"status": row.status, "error": row.error_message},
    )
    return session_to_out(row, config)
