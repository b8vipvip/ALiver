from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.avatar_action_service import schedule_director_command_action
from app.extension_hub import extension_hub
from app.json_utils import loads
from app.models import DirectorCommand


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def command_to_dict(row: DirectorCommand) -> dict[str, Any]:
    return {
        "id": row.id,
        "extension_id": row.extension_id,
        "command_type": row.command_type,
        "payload": loads(row.payload_json, {}),
        "result": loads(row.result_json, {}),
        "status": row.status,
        "priority": row.priority,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "dispatched_at": row.dispatched_at,
        "completed_at": row.completed_at,
    }


async def dispatch_command(db: Session, row: DirectorCommand) -> bool:
    if not extension_hub.is_connected(row.extension_id):
        row.status = "queued"
        db.commit()
        return False
    await extension_hub.send_command(
        row.extension_id,
        command_id=row.id,
        command_type=row.command_type,
        payload=loads(row.payload_json, {}),
    )
    row.status = "dispatched"
    row.dispatched_at = utcnow()
    row.error_message = None
    db.commit()
    schedule_director_command_action(row)
    return True


async def dispatch_queued(db: Session, extension_id: str, *, limit: int = 50) -> int:
    rows = db.scalars(
        select(DirectorCommand)
        .where(
            DirectorCommand.extension_id == extension_id,
            DirectorCommand.status == "queued",
        )
        .order_by(DirectorCommand.priority.desc(), DirectorCommand.created_at.asc())
        .limit(limit)
    ).all()
    sent = 0
    for row in rows:
        try:
            if await dispatch_command(db, row):
                sent += 1
        except RuntimeError:
            break
    return sent


def requeue_dispatched(db: Session, extension_id: str) -> int:
    rows = db.scalars(
        select(DirectorCommand).where(
            DirectorCommand.extension_id == extension_id,
            DirectorCommand.status == "dispatched",
        )
    ).all()
    for row in rows:
        row.status = "queued"
        row.dispatched_at = None
    db.commit()
    return len(rows)
