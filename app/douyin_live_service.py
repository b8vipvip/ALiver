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

PLATFORM = "douyin_live_companion"
OFFICIAL_MESSAGE_TYPES = {
    "live_like": 1,
    "live_comment": 2,
    "live_gift": 3,
    "live_fansclub": 4,
    "live_follow": 5,
}
TYPE_BY_NUMBER = {value: key for key, value in OFFICIAL_MESSAGE_TYPES.items()}


@dataclass
class CollectorRuntime:
    extension_id: str
    collector_id: str = "douyin-live-companion"
    connected: bool = False
    last_seen_at: datetime | None = None
    last_batch_at: datetime | None = None
    last_error: str | None = None
    mate_version: str | None = None
    layout_mode: int | None = None
    plugin_version: str | None = None
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    ignored: int = 0
    failed: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    recent: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=30))


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
    connected: bool,
    mate_version: str | None = None,
    layout_mode: int | None = None,
    plugin_version: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    runtime = _runtime(extension_id, collector_id)
    with _lock:
        runtime.connected = connected
        runtime.last_seen_at = _now()
        runtime.mate_version = mate_version or runtime.mate_version
        runtime.layout_mode = layout_mode if layout_mode is not None else runtime.layout_mode
        runtime.plugin_version = plugin_version or runtime.plugin_version
        runtime.last_error = error
    return collector_status(extension_id)


def collector_status(extension_id: str) -> dict[str, Any]:
    runtime = _runtime(extension_id)
    now = _now()
    last_seen = runtime.last_seen_at
    healthy = bool(runtime.connected and last_seen and (now - last_seen).total_seconds() <= 20)
    with _lock:
        return {
            "extension_id": extension_id,
            "collector_id": runtime.collector_id,
            "connected": healthy,
            "reported_connected": runtime.connected,
            "last_seen_at": last_seen,
            "last_batch_at": runtime.last_batch_at,
            "last_error": runtime.last_error,
            "mate_version": runtime.mate_version,
            "layout_mode": runtime.layout_mode,
            "plugin_version": runtime.plugin_version,
            "received": runtime.received,
            "accepted": runtime.accepted,
            "duplicates": runtime.duplicates,
            "ignored": runtime.ignored,
            "failed": runtime.failed,
            "counts": dict(runtime.counts),
            "recent": list(runtime.recent),
            "official_message_types": list(OFFICIAL_MESSAGE_TYPES),
            "share_support": "compatibility_only",
        }


def _message_type(item: dict[str, Any]) -> str:
    raw = str(item.get("msg_type_str") or "").strip().lower()
    if raw:
        return raw
    try:
        return TYPE_BY_NUMBER.get(int(item.get("msg_type") or 0), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _gift_content(item: dict[str, Any]) -> str:
    name = str(item.get("gift_name") or item.get("sec_gift_id") or "礼物").strip()
    count = max(1, int(item.get("gift_num") or 1))
    return f"送出了{name} × {count}"


def normalize_message(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    kind = _message_type(item)
    nickname = str(item.get("nickname") or "观众").strip()[:160] or "观众"

    if kind == "live_comment":
        content = str(item.get("content") or "").strip()
        if not content:
            return None, "空评论"
        return {"event_type": "comment", "user_name": nickname, "content": content}, ""
    if kind == "live_gift":
        return {"event_type": "gift", "user_name": nickname, "content": _gift_content(item)}, ""
    if kind == "live_like":
        count = max(1, int(item.get("like_num") or 1))
        return {"event_type": "like", "user_name": nickname, "content": f"点赞 × {count}"}, ""
    if kind == "live_follow":
        action = int(item.get("user_follow_action") or item.get("use_follow_action") or 0)
        if action == 2:
            return None, "取消关注不触发主播口播"
        return {"event_type": "follow", "user_name": nickname, "content": "关注了直播间"}, ""
    if kind == "live_fansclub":
        reason = int(item.get("fansclub_reason_type") or 0)
        level = int(item.get("fansclub_level") or 0)
        if reason == 16:
            return None, "退出粉丝团不触发主播口播"
        action = "加入了粉丝团" if reason == 2 else f"粉丝团升级到 {level} 级"
        return {"event_type": "system", "user_name": nickname, "content": action}, ""
    # The official companion schema currently does not document a share message,
    # but keeping this compatibility mapping allows an approved relay to add it later.
    if kind == "live_share":
        return {"event_type": "share", "user_name": nickname, "content": "分享了直播间"}, ""
    return None, f"不支持的消息类型：{kind}"


def _fingerprint(item: dict[str, Any], normalized: dict[str, Any]) -> str:
    message_id = str(item.get("msg_id") or "").strip()
    if message_id:
        raw = f"{PLATFORM}|{message_id}"
    else:
        raw = "|".join(
            [
                PLATFORM,
                str(item.get("timestamp") or ""),
                normalized["event_type"],
                normalized["user_name"],
                normalized["content"],
            ]
        )
    return sha256(raw.encode("utf-8")).hexdigest()


def ingest_open_live_data(
    db: Session,
    config: AutoDirectorConfig,
    *,
    extension_id: str,
    collector_id: str,
    items: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = _runtime(extension_id, collector_id)
    settings = merged_settings(config)
    result = {"received": len(items), "accepted": 0, "duplicates": 0, "ignored": 0, "failed": 0, "events": []}
    metadata = metadata or {}

    with _lock:
        runtime.connected = True
        runtime.last_seen_at = _now()
        runtime.last_batch_at = _now()
        runtime.received += len(items)
        runtime.mate_version = str(metadata.get("mate_version") or runtime.mate_version or "") or None
        if metadata.get("layout_mode") is not None:
            runtime.layout_mode = int(metadata["layout_mode"])
        runtime.plugin_version = str(metadata.get("plugin_version") or runtime.plugin_version or "") or None

    try:
        for item in items:
            normalized, ignore_reason = normalize_message(item)
            kind = _message_type(item)
            with _lock:
                runtime.counts[kind] += 1
            if normalized is None:
                result["ignored"] += 1
                with _lock:
                    runtime.ignored += 1
                    runtime.recent.append({"type": kind, "status": "ignored", "reason": ignore_reason, "at": _now().isoformat()})
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
            payload["douyin_collector"] = {
                "collector_id": collector_id,
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
            result["events"].append(
                {"event_type": row.event_type, "user_name": row.user_name, "content": row.content, "status": status, "score": score}
            )
            with _lock:
                runtime.accepted += 1
                runtime.recent.append(
                    {
                        "type": kind,
                        "event_type": row.event_type,
                        "user_name": row.user_name,
                        "content": row.content[:120],
                        "status": status,
                        "score": score,
                        "at": _now().isoformat(),
                    }
                )

        config.last_event_at = utcnow()
        db.commit()
        write_log(
            db,
            category="douyin.collector.batch",
            message=f"Douyin collector accepted {result['accepted']} of {result['received']} messages",
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
