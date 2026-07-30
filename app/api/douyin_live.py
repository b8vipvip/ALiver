from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.auto_director_service import config_to_dict
from app.db import get_db
from app.douyin_live_service import collector_heartbeat, collector_status, ingest_open_live_data
from app.models import AutoDirectorConfig, BrowserExtension

router = APIRouter(
    prefix="/api/douyin-live",
    tags=["douyin-live"],
    dependencies=[Depends(require_admin_token)],
)


class DouyinHeartbeat(BaseModel):
    extension_id: str
    collector_id: str = Field(default="douyin-live-companion", max_length=120)
    connected: bool = True
    mate_version: str | None = Field(default=None, max_length=80)
    layout_mode: int | None = Field(default=None, ge=0, le=1)
    plugin_version: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=1000)


class DouyinOpenLiveData(BaseModel):
    extension_id: str
    collector_id: str = Field(default="douyin-live-companion", max_length=120)
    event_name: str = Field(default="OPEN_LIVE_DATA", max_length=100)
    payload: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


def require_extension_and_config(db: Session, extension_id: str) -> tuple[BrowserExtension, AutoDirectorConfig]:
    extension = db.get(BrowserExtension, extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome director extension not found")
    config = db.query(AutoDirectorConfig).filter(AutoDirectorConfig.extension_id == extension_id).one_or_none()
    if not config:
        raise HTTPException(status_code=409, detail="请先为该 Chrome 扩展保存自动导演配置")
    return extension, config


@router.post("/heartbeat")
def heartbeat(payload: DouyinHeartbeat, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_extension_and_config(db, payload.extension_id)
    return collector_heartbeat(
        payload.extension_id,
        collector_id=payload.collector_id,
        connected=payload.connected,
        mate_version=payload.mate_version,
        layout_mode=payload.layout_mode,
        plugin_version=payload.plugin_version,
        error=payload.error,
    )


@router.post("/ingest")
def ingest(payload: DouyinOpenLiveData, db: Session = Depends(get_db)) -> dict[str, Any]:
    _, config = require_extension_and_config(db, payload.extension_id)
    if payload.event_name != "OPEN_LIVE_DATA":
        raise HTTPException(status_code=422, detail="Only OPEN_LIVE_DATA is accepted")
    if not payload.payload:
        return {
            "received": 0,
            "accepted": 0,
            "duplicates": 0,
            "ignored": 0,
            "failed": 0,
            "events": [],
        }
    return ingest_open_live_data(
        db,
        config,
        extension_id=payload.extension_id,
        collector_id=payload.collector_id,
        items=payload.payload,
        metadata=payload.metadata,
    )


@router.get("/status")
def status(
    extension_id: str = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, config = require_extension_and_config(db, extension_id)
    value = collector_status(extension_id)
    value["auto_director"] = config_to_dict(config, extension_id)
    return value


@router.post("/simulate")
def simulate(
    payload: DouyinOpenLiveData,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _, config = require_extension_and_config(db, payload.extension_id)
    items = payload.payload or [
        {
            "msg_id": "aliver-sim-comment-1",
            "timestamp": 0,
            "msg_type": 2,
            "msg_type_str": "live_comment",
            "sec_open_id": "debug-user",
            "nickname": "抖音测试观众",
            "content": "数字人直播是怎么实现的？",
        },
        {
            "msg_id": "aliver-sim-gift-1",
            "timestamp": 1,
            "msg_type": 3,
            "msg_type_str": "live_gift",
            "sec_open_id": "debug-gifter",
            "nickname": "礼物测试观众",
            "gift_name": "小心心",
            "gift_num": 1,
            "sec_gift_id": "debug-gift",
        },
        {
            "msg_id": "aliver-sim-follow-1",
            "timestamp": 2,
            "msg_type": 5,
            "msg_type_str": "live_follow",
            "sec_open_id": "debug-follower",
            "nickname": "新关注观众",
            "user_follow_action": 1,
        },
    ]
    return ingest_open_live_data(
        db,
        config,
        extension_id=payload.extension_id,
        collector_id=payload.collector_id or "douyin-simulator",
        items=items,
        metadata={**payload.metadata, "simulated": True},
    )
