from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.common import provider_to_out
from app.auth import require_admin_token
from app.db import get_db
from app.json_utils import dumps
from app.log_service import write_log
from app.models import AvatarSession, ProviderConfig
from app.provider_manager import build_provider
from app.schemas import ProviderCreate, ProviderOut, ProviderUpdate
from app.security import encrypt_json

router = APIRouter(
    prefix="/api/providers",
    tags=["providers"],
    dependencies=[Depends(require_admin_token)],
)

ACTIVE_SESSION_STATUSES = {
    "starting",
    "active",
    "running",
    "ready",
    "awaiting_manual",
    "reconnecting",
    "stop_failed",
}


@router.get("", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)) -> list[ProviderOut]:
    rows = db.scalars(select(ProviderConfig).order_by(ProviderConfig.created_at.desc())).all()
    return [provider_to_out(row) for row in rows]


@router.post("", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderCreate, db: Session = Depends(get_db)) -> ProviderOut:
    row = ProviderConfig(
        name=payload.name,
        provider_type=payload.provider_type,
        enabled=payload.enabled,
        api_base_url=payload.api_base_url,
        credentials_encrypted=encrypt_json(payload.credentials),
        settings_json=dumps(payload.settings),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider name already exists") from exc
    db.refresh(row)
    write_log(
        db,
        category="provider.created",
        message=f"Provider created: {row.name}",
        provider_id=row.id,
        details={"provider_type": row.provider_type},
    )
    return provider_to_out(row)


@router.patch("/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    db: Session = Depends(get_db),
) -> ProviderOut:
    row = db.get(ProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        row.name = values["name"]
    if "enabled" in values:
        row.enabled = values["enabled"]
    if "api_base_url" in values:
        row.api_base_url = values["api_base_url"]
    if "credentials" in values:
        row.credentials_encrypted = encrypt_json(values["credentials"] or {})
    if "settings" in values:
        row.settings_json = dumps(values["settings"] or {})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Provider name already exists") from exc
    db.refresh(row)
    write_log(
        db,
        category="provider.updated",
        message=f"Provider updated: {row.name}",
        provider_id=row.id,
        details={"updated_fields": sorted(values)},
    )
    return provider_to_out(row)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
) -> None:
    row = db.get(ProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")

    sessions = list(
        db.scalars(
            select(AvatarSession).where(AvatarSession.provider_config_id == provider_id)
        ).all()
    )
    active = [session for session in sessions if session.status in ACTIVE_SESSION_STATUSES]
    if active:
        raise HTTPException(
            status_code=409,
            detail="该供应商仍有运行中或停止失败的会话，请先停止这些会话再删除。",
        )
    if sessions and not force:
        raise HTTPException(
            status_code=409,
            detail="该供应商存在会话历史。确认删除供应商及其会话历史后，请使用 force=true。",
        )

    session_ids = [session.id for session in sessions]
    for session in sessions:
        db.delete(session)
    db.delete(row)
    db.commit()
    write_log(
        db,
        category="provider.deleted",
        message=f"Provider deleted: {row.name}",
        provider_id=provider_id,
        details={
            "provider_type": row.provider_type,
            "deleted_session_count": len(session_ids),
            "deleted_session_ids": session_ids,
        },
    )


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(ProviderConfig, provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider = build_provider(row)
    result = await provider.test_connection()
    write_log(
        db,
        category="provider.test",
        message=f"Provider test {'succeeded' if result.success else 'failed'}: {row.name}",
        level="INFO" if result.success else "ERROR",
        provider_id=row.id,
        details=result.data if result.success else {"error": result.error, "data": result.data},
        latency_ms=result.latency_ms,
    )
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "latency_ms": result.latency_ms,
    }
