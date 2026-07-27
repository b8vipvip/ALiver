from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.db import get_db
from app.director_service import command_to_dict, dispatch_command
from app.extension_hub import extension_hub
from app.json_utils import dumps, loads
from app.log_service import write_log
from app.models import BrowserExtension, DirectorCommand
from app.schemas import (
    BrowserExtensionOut,
    BrowserExtensionRegister,
    BrowserExtensionRegisterOut,
    DirectorCommandCreate,
    DirectorCommandOut,
)
from app.security import generate_token, hash_token

router = APIRouter(prefix="/api/director", tags=["director"])


def _extension_out(row: BrowserExtension) -> BrowserExtensionOut:
    return BrowserExtensionOut(
        id=row.id,
        name=row.name,
        browser_name=row.browser_name,
        version=row.version,
        status=row.status,
        connected=extension_hub.is_connected(row.id),
        active_tab_url=row.active_tab_url,
        metadata=loads(row.metadata_json, {}),
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/extensions/register",
    response_model=BrowserExtensionRegisterOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_token)],
)
def register_extension(
    payload: BrowserExtensionRegister,
    db: Session = Depends(get_db),
) -> BrowserExtensionRegisterOut:
    token = generate_token()
    row = BrowserExtension(
        name=payload.name,
        browser_name=payload.browser_name,
        version=payload.version,
        metadata_json=dumps(payload.metadata),
        token_hash=hash_token(token),
        status="offline",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    write_log(
        db,
        category="director.extension.registered",
        message=f"Chrome extension registered: {row.name}",
        details={"extension_id": row.id, "browser": row.browser_name, "version": row.version},
    )
    return BrowserExtensionRegisterOut(extension_id=row.id, token=token)


@router.get(
    "/extensions",
    response_model=list[BrowserExtensionOut],
    dependencies=[Depends(require_admin_token)],
)
def list_extensions(db: Session = Depends(get_db)) -> list[BrowserExtensionOut]:
    rows = db.scalars(select(BrowserExtension).order_by(BrowserExtension.created_at.desc())).all()
    return [_extension_out(row) for row in rows]


@router.get(
    "/commands",
    response_model=list[DirectorCommandOut],
    dependencies=[Depends(require_admin_token)],
)
def list_commands(
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[DirectorCommandOut]:
    limit = min(max(limit, 1), 500)
    rows = db.scalars(
        select(DirectorCommand).order_by(DirectorCommand.created_at.desc()).limit(limit)
    ).all()
    return [DirectorCommandOut(**command_to_dict(row)) for row in rows]


def _director_text(payload: DirectorCommandCreate) -> str:
    text = payload.content.strip()
    if payload.wrap_as_director:
        return (
            "【导演指令】\n"
            "这是后台控制信息，不要朗读指令本身，也不要提到导演或后台。"
            "请只执行要求，并用自然口语给出最终回答。\n\n"
            f"{text}"
        )
    return text


@router.post(
    "/commands",
    response_model=DirectorCommandOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_token)],
)
async def create_command(
    payload: DirectorCommandCreate,
    db: Session = Depends(get_db),
) -> DirectorCommandOut:
    extension = db.get(BrowserExtension, payload.extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    command_payload = {
        "text": _director_text(payload),
        "auto_send": payload.auto_send,
        "force": payload.force,
        "source": payload.source,
    }
    row = DirectorCommand(
        extension_id=extension.id,
        command_type=payload.command_type,
        payload_json=dumps(command_payload),
        status="queued",
        priority=payload.priority,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    dispatched = False
    try:
        dispatched = await dispatch_command(db, row)
    except RuntimeError as exc:
        row.status = "queued"
        row.error_message = str(exc)
        db.commit()
    write_log(
        db,
        category="director.command.created",
        message=f"Director command {'dispatched' if dispatched else 'queued'}: {row.command_type}",
        details={"command_id": row.id, "extension_id": row.extension_id, "priority": row.priority},
    )
    db.refresh(row)
    return DirectorCommandOut(**command_to_dict(row))


@router.post(
    "/commands/{command_id}/retry",
    response_model=DirectorCommandOut,
    dependencies=[Depends(require_admin_token)],
)
async def retry_command(command_id: str, db: Session = Depends(get_db)) -> DirectorCommandOut:
    row = db.get(DirectorCommand, command_id)
    if not row:
        raise HTTPException(status_code=404, detail="Director command not found")
    row.status = "queued"
    row.error_message = None
    row.result_json = "{}"
    row.completed_at = None
    row.dispatched_at = None
    db.commit()
    try:
        await dispatch_command(db, row)
    except RuntimeError as exc:
        row.error_message = str(exc)
        db.commit()
    db.refresh(row)
    return DirectorCommandOut(**command_to_dict(row))


@router.post(
    "/commands/{command_id}/cancel",
    response_model=DirectorCommandOut,
    dependencies=[Depends(require_admin_token)],
)
def cancel_command(command_id: str, db: Session = Depends(get_db)) -> DirectorCommandOut:
    row = db.get(DirectorCommand, command_id)
    if not row:
        raise HTTPException(status_code=404, detail="Director command not found")
    if row.status in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail=f"Cannot cancel a {row.status} command")
    row.status = "cancelled"
    row.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return DirectorCommandOut(**command_to_dict(row))
