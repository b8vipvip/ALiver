from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.auto_director_service import config_to_dict
from app.db import get_db
from app.douyin_live_service import collector_heartbeat, collector_status, ingest_visible_events
from app.models import AutoDirectorConfig, BridgeAgent, BrowserExtension
from app.security import verify_token

router = APIRouter(prefix="/api/douyin-live", tags=["douyin-live"])


class VisibleCollectorHeartbeat(BaseModel):
    bridge_id: str
    extension_id: str
    collector_id: str = Field(default="douyin-visible-ui", max_length=120)
    connected: bool = True
    mode: str = Field(default="hybrid", pattern="^(hybrid|uia|ocr)$")
    window_title: str | None = Field(default=None, max_length=300)
    uia_available: bool | None = None
    ocr_available: bool | None = None
    active_source: str | None = Field(default=None, max_length=40)
    error: str | None = Field(default=None, max_length=2000)


class VisibleCollectorBatch(BaseModel):
    bridge_id: str
    extension_id: str
    collector_id: str = Field(default="douyin-visible-ui", max_length=120)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VisibleCollectorAdminRequest(BaseModel):
    extension_id: str
    collector_id: str = Field(default="douyin-visible-simulator", max_length=120)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


def require_extension_and_config(db: Session, extension_id: str) -> tuple[BrowserExtension, AutoDirectorConfig]:
    extension = db.get(BrowserExtension, extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome director extension not found")
    config = db.query(AutoDirectorConfig).filter(AutoDirectorConfig.extension_id == extension_id).one_or_none()
    if not config:
        raise HTTPException(status_code=409, detail="请先为该 Chrome 扩展保存自动导演配置")
    return extension, config


def require_bridge(db: Session, bridge_id: str, token: str | None) -> BridgeAgent:
    bridge = db.get(BridgeAgent, bridge_id)
    if not bridge or not token or not verify_token(token, bridge.token_hash):
        raise HTTPException(status_code=401, detail="Invalid bridge credentials")
    return bridge


@router.post("/bridge/heartbeat")
def bridge_heartbeat(
    payload: VisibleCollectorHeartbeat,
    x_bridge_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    bridge = require_bridge(db, payload.bridge_id, x_bridge_token)
    require_extension_and_config(db, payload.extension_id)
    return collector_heartbeat(
        payload.extension_id,
        collector_id=payload.collector_id,
        bridge_id=bridge.id,
        connected=payload.connected,
        mode=payload.mode,
        window_title=payload.window_title,
        uia_available=payload.uia_available,
        ocr_available=payload.ocr_available,
        active_source=payload.active_source,
        error=payload.error,
    )


@router.post("/bridge/ingest")
def bridge_ingest(
    payload: VisibleCollectorBatch,
    x_bridge_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    bridge = require_bridge(db, payload.bridge_id, x_bridge_token)
    _, config = require_extension_and_config(db, payload.extension_id)
    return ingest_visible_events(
        db,
        config,
        extension_id=payload.extension_id,
        collector_id=payload.collector_id,
        bridge_id=bridge.id,
        items=payload.events,
        metadata=payload.metadata,
    )


@router.get("/status", dependencies=[Depends(require_admin_token)])
def status(
    extension_id: str = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, config = require_extension_and_config(db, extension_id)
    value = collector_status(extension_id)
    value["auto_director"] = config_to_dict(config, extension_id)
    return value


@router.post("/simulate", dependencies=[Depends(require_admin_token)])
def simulate(
    payload: VisibleCollectorAdminRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, config = require_extension_and_config(db, payload.extension_id)
    events = payload.events or [
        {
            "event_id": "visible-sim-comment-1",
            "event_type": "comment",
            "user_name": "抖音测试观众",
            "content": "数字人直播是怎么实现的？",
            "source": "uia",
            "confidence": 1.0,
            "raw_text": "抖音测试观众 数字人直播是怎么实现的？",
        },
        {
            "event_id": "visible-sim-gift-1",
            "event_type": "gift",
            "user_name": "礼物测试观众",
            "content": "送出了小心心 × 1",
            "source": "ocr",
            "confidence": 0.96,
            "raw_text": "礼物测试观众 送出了小心心",
        },
        {
            "event_id": "visible-sim-follow-1",
            "event_type": "follow",
            "user_name": "新关注观众",
            "content": "关注了直播间",
            "source": "uia",
            "confidence": 1.0,
            "raw_text": "新关注观众 关注了你",
        },
    ]
    return ingest_visible_events(
        db,
        config,
        extension_id=payload.extension_id,
        collector_id=payload.collector_id,
        bridge_id=None,
        items=events,
        metadata={**payload.metadata, "simulated": True, "mode": "hybrid"},
    )
