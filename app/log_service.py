from typing import Any

from sqlalchemy.orm import Session

from app.json_utils import dumps
from app.models import EventLog


def write_log(
    db: Session,
    *,
    category: str,
    message: str,
    level: str = "INFO",
    details: dict[str, Any] | None = None,
    provider_id: str | None = None,
    session_id: str | None = None,
    bridge_id: str | None = None,
    latency_ms: int | None = None,
) -> EventLog:
    row = EventLog(
        level=level.upper(),
        category=category,
        message=message,
        details_json=dumps(details or {}),
        provider_id=provider_id,
        session_id=session_id,
        bridge_id=bridge_id,
        latency_ms=latency_ms,
    )
    db.add(row)
    db.commit()
    # expire_on_commit=False keeps generated fields available. Calling refresh()
    # here would immediately open another read transaction; several WebSocket
    # callers then await network I/O and could keep that transaction alive.
    return row
