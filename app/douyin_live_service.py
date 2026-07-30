from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from threading import Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auto_director_service import merged_settings, score_event, utcnow
from app.json_utils import dumps
from app.log_service import write_log
from app.models import AudienceEvent, AutoDirectorConfig

PLATFORM = "douyin_visible_ui"
ALLOWED_EVENT_TYPES = {"comment", "gift", "follow", "share", "like", "system"}


@dataclass
class CollectorRuntime:
    extension_id: str
    collector_id: str = "douyin-visible-ui"
    bridge_id: str | None = None
    connected: bool = False
    last_seen_at: datetime | None = None
    last_batch_at: datetime | None = None
    last_error: str | None = None
    mode: str = "hybrid"
    window_title: str | None = None
    uia_available: bool | None = None
    ocr_available: bool | None = None
    active_source: str | None = None
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    ignored: int = 0
    failed: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=40))


_lock = Lock()
_runtimes: dict[str, CollectorRuntime] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _runtime(extension_id: str, collector_id: str | None = None) -> CollectorRuntime:
    with _lock:
        runtime = _runtimes.get(extension_id)
        if runtime is None:
            runtime = CollectorRuntime(extension_id=extension_id)
            _runtimes[extension_id] = runtime
        if collector_id:
            runtime.collector_id = collector_id
        return runtime


def collector_heartbeat(
    extension_id: str,
    *,
    collector_id: str,
    bridge_id: str | None,
    connected: bool,
    mode: str | None = None,
    window_title: str | None = None,
    uia_available: bool | None = None,
    ocr_available: bool | None = None,
    active_source: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    runtime = _runtime(extension_id, collector_id)
    with _lock:
        runtime.bridge_id = bridge_id or runtime.bridge_id
        runtime.connected = connected
        runtime.last_seen_at = _now()
        runtime.mode = str(mode or runtime.mode or "hybrid")
        runtime.window_title = window_title or runtime.window_title
        runtime.uia_available = uia_available if uia_available is not None else runtime.uia_available
        runtime.ocr_available = ocr_available if ocr_available is not None else runtime.ocr_available
        runtime.active_source = active_source or runtime.active_source
        runtime.last_error = error
    return collector_status(extension_id)


def collector_status(extension_id: str) -> dict[str, Any]:
    runtime = _runtime(extension_id)
    with _lock:
        last_seen = runtime.last_seen_at
        healthy = bool(runtime.connected and last_seen and (_now() - last_seen).total_seconds() <= 20)
        return {
            "extension_id": extension_id,
            "collector_id": runtime.collector_id,
            "bridge_id": runtime.bridge_id,
            "connected": healthy,
            "reported_connected": runtime.connected,
            "last_seen_at": last_seen,
            "last_batch_at": runtime.last_batch_at,
            "last_error": runtime.last_error,
            "mode": runtime.mode,
            "window_title": runtime.window_title,
            "uia_available": runtime.uia_available,
            "ocr_available": runtime.ocr_available,
            "active_source": runtime.active_source,
            "received": runtime.received,
            "accepted": runtime.accepted,
            "duplicates": runtime.duplicates,
            "ignored": runtime.ignored,
            "failed": runtime.failed,
            "counts": dict(runtime.counts),
            "recent": list(runtime.recent),
            "capture_policy": "visible_ui_only",
        }


def _fingerprint(item: dict[str, Any], normalized: dict[str, Any]) -> str:
    event_id = str(item.get("event_id") or "").strip()
    if event_id:
        raw = f"{PLATFORM}|{event_id}"
    else:
        raw = "|".join(
            [
                PLATFORM,
                normalized["event_type"],
                normalized["user_name"],
                normalized["content"],
                str(item.get("source") or ""),
                str(item.get("observed_at") or ""),
            ]
        )
    return sha256(raw.encode("utf-8")).hexdigest()


def normalize_visible_event(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    event_type = str(item.get("event_type") or "comment").strip().lower()
    if event_type == "join":
        event_type = "system"
    if event_type not in ALLOWED_EVENT_TYPES:
        return None, f"不支持的可视事件类型：{event_type}"

    user_name = str(item.get("user_name") or "观众").strip()[:160] or "观众"
    content = str(item.get("content") or "").strip()[:2000]
    confidence = float(item.get("confidence") or 0.0)
    threshold = float(item.get("confidence_threshold") or 0.72)
    if confidence and confidence < threshold:
        return None, f"识别置信度 {confidence:.2f} 低于阈值 {threshold:.2f}"
    if event_type == "comment" and not content:
        return None, "空评论"
    if event_type == "follow" and not content:
        content = "关注了直播间"
    if event_type == "gift" and not content:
        content = "送出了礼物"
    if event_type == "share" and not content:
        content = "分享了直播间"
    if event_type == "like" and not content:
        content = "点赞了直播间"
    if event_type == "system" and not content:
        return None, "空系统通知"
    return {"event_type": event_type, "user_name": user_name, "content": content}, ""


def ingest_visible_events(
    db: Session,
    config: AutoDirectorConfig,
    *,
    extension_id: str,
    collector_id: str,
    bridge_id: str | None,
    items: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = _runtime(extension_id, collector_id)
    settings = merged_settings(config)
    result = {"received": len(items), "accepted": 0, "duplicates": 0, "ignored": 0, "failed": 0, "events": []}
    metadata = metadata or {}

    collector_heartbeat(
        extension_id,
        collector_id=collector_id,
        bridge_id=bridge_id,
        connected=True,
        mode=str(metadata.get("mode") or runtime.mode),
        window_title=str(metadata.get("window_title") or runtime.window_title or "") or None,
        uia_available=metadata.get("uia_available"),
        ocr_available=metadata.get("ocr_available"),
        active_source=str(metadata.get("active_source") or "") or None,
        error=None,
    )
    with _lock:
        runtime.last_batch_at = _now()
        runtime.received += len(items)

    try:
        for item in items:
            normalized, ignore_reason = normalize_visible_event(item)
            source = str(item.get("source") or "unknown")
            if normalized is None:
                result["ignored"] += 1
                with _lock:
                    runtime.ignored += 1
                    runtime.recent.append(
                        {
                            "source": source,
                            "status": "ignored",
                            "reason": ignore_reason,
                            "raw_text": str(item.get("raw_text") or "")[:240],
                            "at": _now().isoformat(),
                        }
                    )
                continue

            fingerprint = _fingerprint(item, normalized)
            duplicate = db.scalar(
                select(AudienceEvent.id)
                .where(AudienceEvent.config_id == config.id, AudienceEvent.fingerprint == fingerprint)
                .limit(1)
            )
            if duplicate:
                result["duplicates"] += 1
                with _lock:
                    runtime.duplicates += 1
                continue

            status, score, reason = score_event(
                normalized["event_type"], normalized["user_name"], normalized["content"], settings
            )
            payload = dict(item)
            payload["visible_collector"] = {
                "collector_id": collector_id,
                "bridge_id": bridge_id,
                "extension_id": extension_id,
                **metadata,
            }
            row = AudienceEvent(
                config_id=config.id,
                event_type=normalized["event_type"],
                platform=PLATFORM,
                user_name=normalized["user_name"],
                content=normalized["content"],
                fingerprint=fingerprint,
                payload_json=dumps(payload),
                status=status,
                score=score,
                reason=reason,
                processed_at=utcnow() if status == "ignored" else None,
            )
            db.add(row)
            result["accepted"] += 1
            event_summary = {
                "event_type": row.event_type,
                "user_name": row.user_name,
                "content": row.content,
                "status": status,
                "score": score,
                "source": source,
                "confidence": item.get("confidence"),
            }
            result["events"].append(event_summary)
            with _lock:
                runtime.accepted += 1
                runtime.counts[row.event_type] += 1
                runtime.active_source = source
                runtime.recent.append({**event_summary, "at": _now().isoformat()})

        config.last_event_at = utcnow()
        db.commit()
        write_log(
            db,
            category="douyin.visible.batch",
            message=f"Douyin visible collector accepted {result['accepted']} of {result['received']} events",
            bridge_id=bridge_id,
            details={"extension_id": extension_id, "collector_id": collector_id, **result, "events": result["events"][:10]},
        )
        return result
    except Exception as exc:
        db.rollback()
        result["failed"] += 1
        with _lock:
            runtime.failed += 1
            runtime.last_error = f"{type(exc).__name__}: {exc}"
        raise
