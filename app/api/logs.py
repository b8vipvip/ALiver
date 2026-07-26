from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import log_to_out
from app.auth import require_admin_token
from app.db import get_db
from app.models import EventLog
from app.schemas import LogOut

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("", response_model=list[LogOut])
def list_logs(
    level: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    bridge_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[LogOut]:
    query = select(EventLog)
    if level:
        query = query.where(EventLog.level == level.upper())
    if category:
        query = query.where(EventLog.category == category)
    if session_id:
        query = query.where(EventLog.session_id == session_id)
    if bridge_id:
        query = query.where(EventLog.bridge_id == bridge_id)
    rows = db.scalars(query.order_by(EventLog.created_at.desc()).limit(limit)).all()
    return [log_to_out(row) for row in rows]


@router.get("/summary")
def log_summary(db: Session = Depends(get_db)) -> dict:
    level_rows = db.execute(
        select(EventLog.level, func.count(EventLog.id)).group_by(EventLog.level)
    ).all()
    category_rows = db.execute(
        select(EventLog.category, func.count(EventLog.id))
        .group_by(EventLog.category)
        .order_by(func.count(EventLog.id).desc())
        .limit(10)
    ).all()
    latency = db.execute(
        select(
            func.avg(EventLog.latency_ms),
            func.min(EventLog.latency_ms),
            func.max(EventLog.latency_ms),
        ).where(EventLog.latency_ms.is_not(None))
    ).one()
    return {
        "levels": {level: count for level, count in level_rows},
        "top_categories": [{"category": category, "count": count} for category, count in category_rows],
        "latency_ms": {
            "average": round(float(latency[0]), 2) if latency[0] is not None else None,
            "minimum": latency[1],
            "maximum": latency[2],
        },
    }
