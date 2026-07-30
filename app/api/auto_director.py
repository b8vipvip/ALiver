from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.auto_director_service import (
    config_to_dict,
    event_fingerprint,
    event_to_dict,
    merged_settings,
    process_config,
    score_event,
    utcnow,
)
from app.db import get_db
from app.extension_hub import extension_hub
from app.json_utils import dumps, loads
from app.log_service import write_log
from app.models import (
    AudienceEvent,
    AutoDirectorConfig,
    AutoDirectorRun,
    BrowserExtension,
    DirectorCommand,
    DirectorDecision,
)
from app.pro_director_service import (
    control_run,
    decision_to_dict,
    get_or_create_run,
    professional_settings,
    run_to_dict,
)
from app.schemas import (
    AudienceEventCreate,
    AudienceEventOut,
    AutoDirectorConfigOut,
    AutoDirectorConfigUpsert,
    AutoDirectorProcessOut,
    AutoDirectorStatusOut,
    DirectorDecisionOut,
    ProfessionalDirectorRunAction,
    ProfessionalDirectorRunOut,
)
from app.security import decrypt_json, encrypt_json

router = APIRouter(
    prefix="/api/auto-director",
    tags=["auto-director"],
    dependencies=[Depends(require_admin_token)],
)


def get_config(db: Session, extension_id: str) -> AutoDirectorConfig | None:
    return db.scalar(
        select(AutoDirectorConfig).where(AutoDirectorConfig.extension_id == extension_id)
    )


def require_config(db: Session, extension_id: str) -> AutoDirectorConfig:
    row = get_config(db, extension_id)
    if not row:
        raise HTTPException(status_code=404, detail="Auto director config not found")
    return row


def require_extension(db: Session, extension_id: str) -> BrowserExtension:
    extension = db.get(BrowserExtension, extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    return extension


@router.get("/config", response_model=AutoDirectorConfigOut)
def read_config(
    extension_id: str = Query(...),
    db: Session = Depends(get_db),
) -> AutoDirectorConfigOut:
    require_extension(db, extension_id)
    return AutoDirectorConfigOut(**config_to_dict(get_config(db, extension_id), extension_id))


@router.put("/config", response_model=AutoDirectorConfigOut)
def upsert_config(
    payload: AutoDirectorConfigUpsert,
    db: Session = Depends(get_db),
) -> AutoDirectorConfigOut:
    require_extension(db, payload.extension_id)
    row = get_config(db, payload.extension_id)
    if not row:
        row = AutoDirectorConfig(extension_id=payload.extension_id)
        db.add(row)

    row.enabled = payload.enabled
    row.mode = payload.mode
    row.api_base_url = payload.api_base_url.strip() if payload.api_base_url else None
    row.model_name = payload.model_name.strip() if payload.model_name else None
    row.settings_json = dumps(payload.settings)
    if payload.api_key is not None and payload.api_key.strip():
        credentials = decrypt_json(row.credentials_encrypted)
        credentials["api_key"] = payload.api_key.strip()
        row.credentials_encrypted = encrypt_json(credentials)
    db.commit()
    db.refresh(row)

    settings = merged_settings(row)
    run = get_or_create_run(db, row, settings)
    if run.status in {"stopped", "paused"}:
        run.rundown_json = dumps(professional_settings(settings)["rundown"])
        db.commit()

    write_log(
        db,
        category="auto_director.config.updated",
        message=f"Updated auto director config for extension {payload.extension_id}",
        details={
            "config_id": row.id,
            "enabled": row.enabled,
            "mode": row.mode,
            "professional_mode": bool(settings.get("professional_mode", True)),
        },
    )
    return AutoDirectorConfigOut(**config_to_dict(row, payload.extension_id))


@router.get("/run", response_model=ProfessionalDirectorRunOut)
def read_run(
    extension_id: str = Query(...),
    db: Session = Depends(get_db),
) -> ProfessionalDirectorRunOut:
    config = require_config(db, extension_id)
    settings = merged_settings(config)
    row = get_or_create_run(db, config, settings)
    return ProfessionalDirectorRunOut(**run_to_dict(row, settings))


@router.post("/run/control", response_model=ProfessionalDirectorRunOut)
def control_professional_run(
    payload: ProfessionalDirectorRunAction,
    db: Session = Depends(get_db),
) -> ProfessionalDirectorRunOut:
    config = require_config(db, payload.extension_id)
    settings = merged_settings(config)
    try:
        row = control_run(db, config, settings, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_log(
        db,
        category="professional_director.run.control",
        message=f"Professional director run action: {payload.action}",
        details={
            "config_id": config.id,
            "run_id": row.id,
            "action": payload.action,
            "status": row.status,
            "phase": row.phase,
        },
    )
    return ProfessionalDirectorRunOut(**run_to_dict(row, settings))


@router.get("/decisions", response_model=list[DirectorDecisionOut])
def list_decisions(
    extension_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DirectorDecisionOut]:
    config = get_config(db, extension_id)
    if not config:
        return []
    rows = db.scalars(
        select(DirectorDecision)
        .where(DirectorDecision.config_id == config.id)
        .order_by(DirectorDecision.created_at.desc())
        .limit(limit)
    ).all()
    return [DirectorDecisionOut(**decision_to_dict(row)) for row in rows]


@router.post("/events", response_model=AudienceEventOut, status_code=201)
def create_event(
    payload: AudienceEventCreate,
    db: Session = Depends(get_db),
) -> AudienceEventOut:
    require_extension(db, payload.extension_id)
    config = get_config(db, payload.extension_id)
    if not config:
        raise HTTPException(status_code=409, detail="Configure auto director before ingesting events")

    settings = merged_settings(config)
    fingerprint = event_fingerprint(payload.event_type, payload.user_name, payload.content)
    dedupe_seconds = int(settings.get("dedupe_window_seconds", 90))
    duplicate_after = utcnow() - timedelta(seconds=max(0, dedupe_seconds))
    duplicate = db.scalar(
        select(AudienceEvent)
        .where(
            AudienceEvent.config_id == config.id,
            AudienceEvent.fingerprint == fingerprint,
            AudienceEvent.created_at >= duplicate_after,
        )
        .order_by(AudienceEvent.created_at.desc())
        .limit(1)
    )
    if duplicate:
        row = AudienceEvent(
            config_id=config.id,
            event_type=payload.event_type,
            platform=payload.platform,
            user_name=payload.user_name,
            content=payload.content,
            fingerprint=fingerprint,
            payload_json=dumps(payload.payload),
            status="ignored",
            score=0,
            reason=f"{dedupe_seconds} 秒内重复事件",
            processed_at=utcnow(),
        )
    else:
        status, score, reason = score_event(
            payload.event_type,
            payload.user_name,
            payload.content,
            settings,
        )
        row = AudienceEvent(
            config_id=config.id,
            event_type=payload.event_type,
            platform=payload.platform,
            user_name=payload.user_name,
            content=payload.content,
            fingerprint=fingerprint,
            payload_json=dumps(payload.payload),
            status=status,
            score=score,
            reason=reason,
            processed_at=utcnow() if status == "ignored" else None,
        )
    db.add(row)
    config.last_event_at = utcnow()
    db.commit()
    db.refresh(row)
    write_log(
        db,
        category="auto_director.event.ingested",
        message=f"Ingested {row.event_type} event from {row.platform}: {row.status}",
        details={
            "config_id": config.id,
            "event_id": row.id,
            "score": row.score,
            "status": row.status,
            "reason": row.reason,
        },
    )
    return AudienceEventOut(**event_to_dict(row))


@router.get("/events", response_model=list[AudienceEventOut])
def list_events(
    extension_id: str = Query(...),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AudienceEventOut]:
    config = get_config(db, extension_id)
    if not config:
        return []
    statement = select(AudienceEvent).where(AudienceEvent.config_id == config.id)
    if status:
        statement = statement.where(AudienceEvent.status == status)
    rows = db.scalars(statement.order_by(AudienceEvent.created_at.desc()).limit(limit)).all()
    return [AudienceEventOut(**event_to_dict(row)) for row in rows]


@router.post("/events/{event_id}/retry", response_model=AudienceEventOut)
def retry_event(event_id: str, db: Session = Depends(get_db)) -> AudienceEventOut:
    row = db.get(AudienceEvent, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audience event not found")
    config = db.get(AutoDirectorConfig, row.config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Auto director config not found")
    settings = merged_settings(config)
    status, score, reason = score_event(row.event_type, row.user_name, row.content, settings)
    if status == "ignored" and score == 0:
        raise HTTPException(status_code=409, detail=reason)
    row.status = "queued"
    row.score = max(score, int(settings.get("min_score", 35)))
    row.reason = f"人工重新排队；{reason}"
    row.selected_command_id = None
    row.processed_at = None
    db.commit()
    db.refresh(row)
    return AudienceEventOut(**event_to_dict(row))


@router.post("/events/{event_id}/dismiss", response_model=AudienceEventOut)
def dismiss_event(event_id: str, db: Session = Depends(get_db)) -> AudienceEventOut:
    row = db.get(AudienceEvent, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Audience event not found")
    if row.status == "selected":
        raise HTTPException(status_code=409, detail="已生成导演命令的事件不能直接忽略")
    row.status = "ignored"
    row.reason = "人工导演忽略"
    row.processed_at = utcnow()
    db.commit()
    db.refresh(row)
    return AudienceEventOut(**event_to_dict(row))


@router.post("/process", response_model=AutoDirectorProcessOut)
async def process_once(
    extension_id: str = Query(...),
    force: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> AutoDirectorProcessOut:
    config = get_config(db, extension_id)
    if not config:
        raise HTTPException(status_code=404, detail="Auto director config not found")
    result = await process_config(db, config, force=force)
    return AutoDirectorProcessOut(**result)


@router.get("/status", response_model=AutoDirectorStatusOut)
def read_status(
    extension_id: str = Query(...),
    db: Session = Depends(get_db),
) -> AutoDirectorStatusOut:
    extension = require_extension(db, extension_id)
    config = get_config(db, extension_id)
    metadata = loads(extension.metadata_json, {})
    counts = {"queued": 0, "selected": 0, "ignored": 0}
    pending_commands = 0
    settings = merged_settings(config)
    run_data = None
    last_decision = None
    if config:
        rows = db.execute(
            select(AudienceEvent.status, func.count(AudienceEvent.id))
            .where(AudienceEvent.config_id == config.id)
            .group_by(AudienceEvent.status)
        ).all()
        counts.update({str(status): int(count) for status, count in rows})
        pending_commands = int(
            db.scalar(
                select(func.count(DirectorCommand.id)).where(
                    DirectorCommand.extension_id == extension_id,
                    DirectorCommand.status.in_(["queued", "dispatched"]),
                )
            )
            or 0
        )
        run = db.scalar(select(AutoDirectorRun).where(AutoDirectorRun.config_id == config.id))
        if run:
            run_data = run_to_dict(run, settings)
        decision = db.scalar(
            select(DirectorDecision)
            .where(DirectorDecision.config_id == config.id)
            .order_by(DirectorDecision.created_at.desc())
            .limit(1)
        )
        if decision:
            last_decision = decision_to_dict(decision)
    return AutoDirectorStatusOut(
        extension_id=extension_id,
        configured=bool(config),
        enabled=bool(config and config.enabled),
        mode=config.mode if config else "rules",
        professional_mode=bool(settings.get("professional_mode", True)),
        extension_connected=extension_hub.is_connected(extension_id),
        chatgpt_open=bool(metadata.get("chatgpt_open")),
        composer_ready=bool(metadata.get("composer_ready")),
        generating=bool(metadata.get("generating")),
        queued_events=counts.get("queued", 0),
        selected_events=counts.get("selected", 0),
        ignored_events=counts.get("ignored", 0),
        pending_commands=pending_commands,
        last_dispatched_at=config.last_dispatched_at if config else None,
        last_event_at=config.last_event_at if config else None,
        run=run_data,
        last_decision=last_decision,
    )
