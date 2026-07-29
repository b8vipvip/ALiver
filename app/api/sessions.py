from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

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

SESSION_NAME_KEY = "_session_name"
ACTIVE_SESSION_STATUSES = {
    "starting",
    "active",
    "running",
    "ready",
    "awaiting_manual",
    "reconnecting",
}
NON_DELETABLE_SESSION_STATUSES = ACTIVE_SESSION_STATUSES | {"stop_failed"}


def bridge_error_summary(bridge_result: dict) -> str:
    detail = bridge_result.get("error_detail") or {}
    if isinstance(detail, dict) and detail.get("message_zh"):
        return str(detail["message_zh"])
    return str(bridge_result.get("error") or "Bridge 执行失败，未返回具体原因。")


def default_session_name(provider_name: str) -> str:
    stamp = datetime.now().strftime("%m%d-%H%M")
    return f"{provider_name}-{stamp}-{uuid4().hex[:4]}"


def split_session_request(value: dict | None) -> tuple[str | None, dict]:
    data = dict(value or {})
    name = str(data.pop(SESSION_NAME_KEY, "") or "").strip() or None
    return name, data


def session_request(name: str, overrides: dict) -> dict:
    value = dict(overrides)
    value[SESSION_NAME_KEY] = name.strip()[:120]
    return value


def row_session_name(row: AvatarSession, provider_name: str) -> str:
    name, _ = split_session_request(loads(row.request_json, {}))
    return name or f"{provider_name}-{row.id[:8]}"


def row_overrides(row: AvatarSession) -> dict:
    _, overrides = split_session_request(loads(row.request_json, {}))
    return overrides


def validate_bridge(
    db: Session,
    provider,
    bridge_id: str | None,
    *,
    require_online: bool,
) -> BridgeAgent | None:
    if provider.execution_mode != "bridge":
        return None
    if not bridge_id:
        raise HTTPException(status_code=422, detail="该供应商必须选择一个在线 Bridge")
    bridge = db.get(BridgeAgent, bridge_id)
    if not bridge:
        raise HTTPException(status_code=404, detail="未找到所选 Bridge")
    if require_online and not bridge_hub.is_connected(bridge.id):
        raise HTTPException(status_code=409, detail="所选 Bridge 当前离线")
    return bridge


async def start_existing_row(
    row: AvatarSession,
    config: ProviderConfig,
    overrides: dict,
    db: Session,
) -> tuple[object, BridgeAgent | None, dict | None]:
    provider = build_provider(config)
    bridge = validate_bridge(
        db,
        provider,
        row.bridge_id,
        require_online=True,
    )

    result = await provider.create_session(overrides)
    bridge_result: dict | None = None
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
                row.bridge_id or "",
                "provider.start_session",
                command_payload,
                get_settings().bridge_command_timeout,
            )
            ok = bool(bridge_result.get("ok"))
            bridge_data = bridge_result.get("data") or {}
            row.status = str(bridge_data.get("status") or ("active" if ok else "failed"))
            row.external_session_id = bridge_data.get("external_session_id")
            row.response_json = dumps(bridge_result)
            row.error_message = None if ok else bridge_error_summary(bridge_result)
        except RuntimeError as exc:
            row.status = "failed"
            row.error_message = f"向 Bridge 下发启动命令失败：{exc}"
            row.response_json = dumps(
                {
                    "error": str(exc),
                    "error_zh": row.error_message,
                    "stage": "bridge_command",
                    "bridge_id": row.bridge_id,
                }
            )
    elif result.success:
        row.status = str(result.data.get("status") or "active")
        row.external_session_id = result.external_session_id
        row.response_json = dumps(result.data)
        row.error_message = None
    else:
        row.status = "failed"
        row.error_message = result.error
        row.response_json = dumps(result.data)

    if row.status in {"active", "running", "awaiting_manual", "ready"}:
        row.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return result, bridge, bridge_result


@router.get("", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionOut]:
    rows = db.scalars(select(AvatarSession).order_by(AvatarSession.created_at.desc())).all()
    providers = {row.id: row for row in db.scalars(select(ProviderConfig)).all()}
    return [session_to_out(row, providers.get(row.provider_config_id)) for row in rows]


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> SessionOut:
    config = db.get(ProviderConfig, payload.provider_config_id)
    if not config:
        raise HTTPException(status_code=404, detail="未找到供应商配置")
    if not config.enabled:
        raise HTTPException(status_code=409, detail="供应商当前已禁用")

    requested_name, overrides = split_session_request(payload.overrides)
    name = requested_name or default_session_name(config.name)
    provider = build_provider(config)
    bridge = validate_bridge(db, provider, payload.bridge_id, require_online=True)

    row = AvatarSession(
        provider_config_id=config.id,
        bridge_id=payload.bridge_id,
        status="starting",
        request_json=dumps(session_request(name, overrides)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    result, bridge, bridge_result = await start_existing_row(row, config, overrides, db)
    log_details = {
        "name": name,
        "status": row.status,
        "error": row.error_message,
        "provider_name": config.name,
        "provider_type": config.provider_type,
        "execution_mode": provider.execution_mode,
        "bridge_name": bridge.name if bridge else None,
        "bridge_machine": bridge.machine_name if bridge else None,
        "bridge_result": bridge_result,
        "provider_result": {
            "success": result.success,
            "error": result.error,
            "latency_ms": result.latency_ms,
        },
        "overrides": overrides,
    }
    write_log(
        db,
        category="session.start",
        message=(
            f"数字人会话启动成功：{name}"
            if row.status != "failed"
            else f"数字人会话启动失败：{name}"
        ),
        level="INFO" if row.status != "failed" else "ERROR",
        provider_id=config.id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details=log_details,
        latency_ms=result.latency_ms,
    )
    return session_to_out(row, config)


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(
    session_id: str,
    payload: dict,
    db: Session = Depends(get_db),
) -> SessionOut:
    row = db.get(AvatarSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="未找到会话")

    allowed = {"name", "provider_config_id", "bridge_id", "overrides"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的会话字段：{', '.join(sorted(unknown))}")

    structural = any(key in payload for key in {"provider_config_id", "bridge_id", "overrides"})
    if structural and row.status in ACTIVE_SESSION_STATUSES:
        raise HTTPException(status_code=409, detail="运行中的会话只能修改名称；请先停止后再修改配置。")

    current_provider = db.get(ProviderConfig, row.provider_config_id)
    if not current_provider:
        raise HTTPException(status_code=409, detail="该会话对应的供应商配置已不存在")

    name = row_session_name(row, current_provider.name)
    overrides = row_overrides(row)

    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="会话名称不能为空")
        name = name[:120]

    if "provider_config_id" in payload:
        provider_id = str(payload.get("provider_config_id") or "").strip()
        provider = db.get(ProviderConfig, provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail="未找到新的供应商配置")
        row.provider_config_id = provider.id
        current_provider = provider

    if "bridge_id" in payload:
        bridge_id = str(payload.get("bridge_id") or "").strip() or None
        if bridge_id and not db.get(BridgeAgent, bridge_id):
            raise HTTPException(status_code=404, detail="未找到新的 Bridge")
        row.bridge_id = bridge_id

    if "overrides" in payload:
        value = payload.get("overrides")
        if not isinstance(value, dict):
            raise HTTPException(status_code=422, detail="overrides 必须是 JSON 对象")
        overrides = dict(value)

    row.request_json = dumps(session_request(name, overrides))
    db.commit()
    db.refresh(row)
    write_log(
        db,
        category="session.updated",
        message=f"数字人会话已修改：{name}",
        provider_id=row.provider_config_id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details={"updated_fields": sorted(payload)},
    )
    return session_to_out(row, current_provider)


@router.post("/{session_id}/restart", response_model=SessionOut)
async def restart_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    row = db.get(AvatarSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="未找到会话")
    if row.status in ACTIVE_SESSION_STATUSES:
        raise HTTPException(status_code=409, detail="该会话当前仍在运行，无需重新启用。")

    config = db.get(ProviderConfig, row.provider_config_id)
    if not config:
        raise HTTPException(status_code=409, detail="该会话对应的供应商配置已不存在")
    if not config.enabled:
        raise HTTPException(status_code=409, detail="供应商当前已禁用，请先启用供应商")

    name = row_session_name(row, config.name)
    overrides = row_overrides(row)
    row.status = "starting"
    row.external_session_id = None
    row.response_json = "{}"
    row.error_message = None
    row.started_at = None
    row.ended_at = None
    db.commit()

    result, bridge, bridge_result = await start_existing_row(row, config, overrides, db)
    write_log(
        db,
        category="session.restart",
        message=f"数字人会话重新启用：{name} / {row.status}",
        level="INFO" if row.status != "failed" else "ERROR",
        provider_id=config.id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details={
            "status": row.status,
            "error": row.error_message,
            "bridge_name": bridge.name if bridge else None,
            "bridge_result": bridge_result,
            "provider_result": {
                "success": result.success,
                "error": result.error,
                "latency_ms": result.latency_ms,
            },
        },
    )
    return session_to_out(row, config)


@router.post("/{session_id}/stop", response_model=SessionOut)
async def stop_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    row = db.get(AvatarSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="未找到会话")
    config = db.get(ProviderConfig, row.provider_config_id)
    if not config:
        raise HTTPException(status_code=409, detail="该会话对应的供应商配置已不存在")
    provider = build_provider(config)
    response_data = loads(row.response_json, {})
    bridge_result: dict | None = None

    if provider.execution_mode == "bridge":
        if not row.bridge_id or not bridge_hub.is_connected(row.bridge_id):
            row.status = "ended_local_only"
            row.ended_at = datetime.now(timezone.utc)
            row.error_message = "Bridge 离线，仅在服务端将会话标记为已结束"
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
                row.error_message = None if ok else bridge_error_summary(bridge_result)
                if ok:
                    row.ended_at = datetime.now(timezone.utc)
                db.commit()
            except RuntimeError as exc:
                row.status = "stop_failed"
                row.error_message = f"向 Bridge 下发停止命令失败：{exc}"
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
        message=f"数字人会话停止结果：{row_session_name(row, config.name)} / {row.status}",
        level="INFO" if row.status in {"ended", "ended_local_only"} else "ERROR",
        provider_id=config.id,
        session_id=row.id,
        bridge_id=row.bridge_id,
        details={
            "status": row.status,
            "error": row.error_message,
            "provider_name": config.name,
            "provider_type": config.provider_type,
            "bridge_result": bridge_result,
        },
    )
    return session_to_out(row, config)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> None:
    row = db.get(AvatarSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="未找到会话")
    if row.status in NON_DELETABLE_SESSION_STATUSES:
        raise HTTPException(status_code=409, detail="该会话仍在运行或停止失败，请先成功停止后再删除。")

    config = db.get(ProviderConfig, row.provider_config_id)
    name = row_session_name(row, config.name if config else "会话")
    provider_id = row.provider_config_id
    bridge_id = row.bridge_id
    db.delete(row)
    db.commit()
    write_log(
        db,
        category="session.deleted",
        message=f"数字人会话已删除：{name}",
        provider_id=provider_id,
        session_id=session_id,
        bridge_id=bridge_id,
    )
