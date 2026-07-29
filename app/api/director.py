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

DIRECTOR_PREFIX = (
    "【导演指令】\n"
    "这是后台控制信息，不要朗读指令本身，也不要提到导演或后台。"
    "请只执行要求，并用自然口语给出最终回答。\n\n"
)


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


def _director_text(content: str, wrap_as_director: bool) -> str:
    text = content.strip()
    return f"{DIRECTOR_PREFIX}{text}" if wrap_as_director else text


def _unwrap_director_text(text: str) -> str:
    return text.removeprefix(DIRECTOR_PREFIX).strip()


def _command_values(row: DirectorCommand) -> dict:
    payload = loads(row.payload_json, {})
    content = str(payload.get("content") or "").strip()
    if not content:
        content = _unwrap_director_text(str(payload.get("text") or ""))
    return {
        "extension_id": row.extension_id,
        "command_type": row.command_type,
        "content": content,
        "wrap_as_director": bool(
            payload.get(
                "wrap_as_director",
                str(payload.get("text") or "").startswith("【导演指令】"),
            )
        ),
        "auto_send": bool(payload.get("auto_send", True)),
        "force": bool(payload.get("force", False)),
        "priority": row.priority,
        "source": str(payload.get("source") or "manual_console"),
    }


def _payload_from_values(values: dict) -> dict:
    content = str(values["content"]).strip()
    return {
        "text": _director_text(content, bool(values["wrap_as_director"])),
        "content": content,
        "wrap_as_director": bool(values["wrap_as_director"]),
        "auto_send": bool(values["auto_send"]),
        "force": bool(values["force"]),
        "source": str(values.get("source") or "manual_console")[:80],
    }


async def _dispatch_or_queue(db: Session, row: DirectorCommand) -> bool:
    dispatched = False
    try:
        dispatched = await dispatch_command(db, row)
    except RuntimeError as exc:
        row.status = "queued"
        row.error_message = str(exc)
        db.commit()
    return dispatched


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
    values = {
        "content": payload.content,
        "wrap_as_director": payload.wrap_as_director,
        "auto_send": payload.auto_send,
        "force": payload.force,
        "source": payload.source,
    }
    row = DirectorCommand(
        extension_id=extension.id,
        command_type=payload.command_type,
        payload_json=dumps(_payload_from_values(values)),
        status="queued",
        priority=payload.priority,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    dispatched = await _dispatch_or_queue(db, row)
    write_log(
        db,
        category="director.command.created",
        message=f"Director command {'dispatched' if dispatched else 'queued'}: {row.command_type}",
        details={"command_id": row.id, "extension_id": row.extension_id, "priority": row.priority},
    )
    db.refresh(row)
    return DirectorCommandOut(**command_to_dict(row))


@router.patch(
    "/commands/{command_id}",
    response_model=DirectorCommandOut,
    dependencies=[Depends(require_admin_token)],
)
async def update_command(
    command_id: str,
    payload: dict,
    db: Session = Depends(get_db),
) -> DirectorCommandOut:
    row = db.get(DirectorCommand, command_id)
    if not row:
        raise HTTPException(status_code=404, detail="Director command not found")
    if row.status == "dispatched":
        raise HTTPException(status_code=409, detail="命令已下发到扩展，不能再编辑。可以等待完成后再复制重发。")

    allowed = {
        "extension_id",
        "command_type",
        "content",
        "wrap_as_director",
        "auto_send",
        "force",
        "priority",
        "source",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"不支持的导演命令字段：{', '.join(sorted(unknown))}")

    values = _command_values(row)
    values.update(payload)

    extension_id = str(values.get("extension_id") or "").strip()
    extension = db.get(BrowserExtension, extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome extension not found")

    command_type = str(values.get("command_type") or "").strip()
    if command_type not in {"send_text", "director_instruction"}:
        raise HTTPException(status_code=422, detail="不支持的命令类型")

    content = str(values.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="指令内容不能为空")
    if len(content) > 12000:
        raise HTTPException(status_code=422, detail="指令内容不能超过 12000 个字符")

    try:
        priority = int(values.get("priority", 50))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="优先级必须是整数") from exc
    if not 0 <= priority <= 100:
        raise HTTPException(status_code=422, detail="优先级必须在 0 到 100 之间")

    values.update(
        {
            "extension_id": extension.id,
            "command_type": command_type,
            "content": content,
            "priority": priority,
        }
    )
    row.extension_id = extension.id
    row.command_type = command_type
    row.payload_json = dumps(_payload_from_values(values))
    row.priority = priority
    row.status = "queued"
    row.result_json = "{}"
    row.error_message = None
    row.dispatched_at = None
    row.completed_at = None
    db.commit()
    await _dispatch_or_queue(db, row)
    db.refresh(row)
    write_log(
        db,
        category="director.command.updated",
        message=f"Director command edited and requeued: {row.command_type}",
        details={"command_id": row.id, "extension_id": row.extension_id, "priority": row.priority},
    )
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
    await _dispatch_or_queue(db, row)
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


@router.delete(
    "/commands/{command_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_token)],
)
def delete_command(command_id: str, db: Session = Depends(get_db)) -> None:
    row = db.get(DirectorCommand, command_id)
    if not row:
        raise HTTPException(status_code=404, detail="Director command not found")
    if row.status == "dispatched":
        raise HTTPException(status_code=409, detail="命令已下发到扩展，暂时不能删除。")
    extension_id = row.extension_id
    command_type = row.command_type
    db.delete(row)
    db.commit()
    write_log(
        db,
        category="director.command.deleted",
        message=f"Director command deleted: {command_type}",
        details={"command_id": command_id, "extension_id": extension_id},
    )
