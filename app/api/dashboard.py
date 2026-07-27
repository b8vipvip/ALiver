from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.bridge_hub import bridge_hub
from app.db import get_db
from app.extension_hub import extension_hub
from app.models import AvatarSession, BridgeAgent, BrowserExtension, EventLog, ProviderConfig

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("")
def dashboard(db: Session = Depends(get_db)) -> dict:
    provider_count = db.scalar(select(func.count(ProviderConfig.id))) or 0
    active_sessions = db.scalar(
        select(func.count(AvatarSession.id)).where(
            AvatarSession.status.in_(["active", "running", "awaiting_manual", "ready"])
        )
    ) or 0
    error_count = db.scalar(select(func.count(EventLog.id)).where(EventLog.level == "ERROR")) or 0
    bridges = db.scalars(select(BridgeAgent)).all()
    extensions = db.scalars(select(BrowserExtension)).all()
    online_bridges = sum(1 for row in bridges if bridge_hub.is_connected(row.id))
    online_extensions = sum(1 for row in extensions if extension_hub.is_connected(row.id))
    return {
        "providers": provider_count,
        "active_sessions": active_sessions,
        "online_bridges": online_bridges,
        "online_extensions": online_extensions,
        "errors": error_count,
    }
