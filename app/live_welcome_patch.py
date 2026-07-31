from __future__ import annotations

from datetime import timedelta
from typing import Any

from app import pro_director_service as director

JOIN_CONTENT_MARKERS = ("进入了直播间", "进入直播间", "来了")


def is_viewer_join(event: Any) -> bool:
    """Return True only for a visible collector viewer-entry event."""
    if event is None or str(getattr(event, "event_type", "") or "").lower() != "system":
        return False
    content = str(getattr(event, "content", "") or "").strip()
    return any(marker in content for marker in JOIN_CONTENT_MARKERS)


def _parse_timestamp(value: Any):
    return director.parse_time(value)


def welcome_allowed(
    state: dict[str, Any],
    settings: dict[str, Any],
    user_name: str,
    *,
    now=None,
) -> tuple[bool, str]:
    """Apply per-viewer, global and rolling one-minute welcome limits."""
    if not bool(settings.get("welcome_viewers_enabled", True)):
        return False, "观众进入欢迎已关闭"
    nickname = str(user_name or "").strip()
    if not nickname or nickname in {"观众", "用户", "匿名观众", "一位观众"}:
        return False, "未识别到可用于打招呼的观众昵称"

    now = now or director.utcnow()
    recent_users = state.get("welcome_recent_users")
    if not isinstance(recent_users, dict):
        recent_users = {}
    previous = _parse_timestamp(recent_users.get(nickname))
    per_user = max(60, int(settings.get("welcome_per_viewer_cooldown_seconds", 1800)))
    if previous and now - previous < timedelta(seconds=per_user):
        return False, f"观众 {nickname} 仍在欢迎冷却期"

    last_at = _parse_timestamp(state.get("welcome_last_at"))
    global_cooldown = max(3, int(settings.get("welcome_global_cooldown_seconds", 12)))
    if last_at and now - last_at < timedelta(seconds=global_cooldown):
        return False, "欢迎口播全局冷却中"

    timestamps = state.get("welcome_timestamps")
    if not isinstance(timestamps, list):
        timestamps = []
    minute_ago = now - timedelta(seconds=60)
    recent = [item for item in (_parse_timestamp(value) for value in timestamps) if item and item >= minute_ago]
    max_per_minute = max(1, min(int(settings.get("welcome_max_per_minute", 3)), 12))
    if len(recent) >= max_per_minute:
        return False, f"一分钟欢迎上限 {max_per_minute} 次已达到"
    return True, "允许欢迎"


def welcome_decision(event: Any, settings: dict[str, Any], score: float = 92.0) -> dict[str, Any]:
    nickname = str(getattr(event, "user_name", "") or "观众").strip() or "观众"
    max_seconds = max(5, min(int(settings.get("welcome_max_response_seconds", 12)), 30))
    return {
        "decision_type": "reply",
        "event_id": str(getattr(event, "id", "") or "") or None,
        "instruction": (
            f"检测到观众“{nickname}”刚进入直播间。请自然、简短地称呼昵称并欢迎对方，"
            f"语气亲切但不要夸张营业，控制在 {max_seconds} 秒以内。"
            "欢迎后立即回到当前话题；不要要求关注、送礼或刷屏。"
        ),
        "avatar_action": str(settings.get("welcome_avatar_action") or "wave"),
        "priority": max(75, min(int(score), 96)),
        "duration_seconds": max_seconds,
        "reason": f"识别到新观众 {nickname} 进入直播间，安排一次限频欢迎",
        "topic": f"欢迎 {nickname}",
        "next_cue_seconds": max(10, int(settings.get("welcome_global_cooldown_seconds", 12))),
    }


def install_live_welcome_patch() -> None:
    if getattr(director, "_aliver_live_welcome_v1", False):
        return

    director.PRO_DEFAULTS.update(
        {
            "welcome_viewers_enabled": True,
            "welcome_per_viewer_cooldown_seconds": 1800,
            "welcome_global_cooldown_seconds": 12,
            "welcome_max_per_minute": 3,
            "welcome_max_response_seconds": 12,
            "welcome_avatar_action": "wave",
        }
    )

    original_default_state = director.default_run_state
    original_candidates = director.candidate_events
    original_rule_decision = director.rule_decision
    original_ai_decision = director.ai_director_decision
    original_apply_state = director.apply_decision_state

    def patched_default_state() -> dict[str, Any]:
        return {
            **original_default_state(),
            "welcome_recent_users": {},
            "welcome_timestamps": [],
            "welcome_last_at": None,
            "welcome_count": 0,
        }

    def patched_candidates(db: Any, config: Any, run: Any, settings: dict[str, Any]) -> list[dict[str, Any]]:
        values = list(original_candidates(db, config, run, settings))
        state = director.run_state(run)
        eligible: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []
        dirty = False
        for item in values:
            event = item.get("event")
            if not is_viewer_join(event):
                others.append(item)
                continue
            allowed, reason = welcome_allowed(state, settings, getattr(event, "user_name", ""))
            if not allowed:
                event.status = "ignored"
                event.reason = reason
                event.processed_at = director.utcnow()
                dirty = True
                continue
            item = dict(item)
            item["director_score"] = max(float(item.get("director_score") or 0.0), 92.0)
            item["welcome_event"] = True
            eligible.append(item)
        if dirty:
            db.commit()
        eligible.sort(key=lambda item: getattr(item.get("event"), "created_at", director.utcnow()))
        return eligible + others

    def patched_rule_decision(
        config: Any,
        run: Any,
        settings: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if candidates and is_viewer_join(candidates[0].get("event")):
            return welcome_decision(
                candidates[0]["event"],
                settings,
                float(candidates[0].get("director_score") or 92.0),
            )
        return original_rule_decision(config, run, settings, candidates)

    async def patched_ai_decision(
        config: Any,
        run: Any,
        settings: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Viewer-entry greetings are deterministic so AI mode cannot omit, delay or overdo them.
        if candidates and is_viewer_join(candidates[0].get("event")):
            return welcome_decision(
                candidates[0]["event"],
                settings,
                float(candidates[0].get("director_score") or 92.0),
            )
        return await original_ai_decision(config, run, settings, candidates)

    def patched_apply_state(
        run: Any,
        settings: dict[str, Any],
        decision: dict[str, Any],
        event: Any,
    ) -> None:
        original_apply_state(run, settings, decision, event)
        if not is_viewer_join(event) or str(decision.get("decision_type") or "") != "reply":
            return
        now = director.utcnow()
        state = director.run_state(run)
        recent_users = state.get("welcome_recent_users")
        if not isinstance(recent_users, dict):
            recent_users = {}
        nickname = str(getattr(event, "user_name", "") or "观众").strip() or "观众"
        recent_users[nickname] = now.isoformat()
        if len(recent_users) > 200:
            ordered = sorted(
                recent_users.items(),
                key=lambda item: _parse_timestamp(item[1]) or now,
                reverse=True,
            )[:200]
            recent_users = dict(ordered)
        timestamps = state.get("welcome_timestamps")
        if not isinstance(timestamps, list):
            timestamps = []
        minute_ago = now - timedelta(seconds=60)
        timestamps = [
            value
            for value in timestamps
            if (_parse_timestamp(value) and _parse_timestamp(value) >= minute_ago)
        ]
        timestamps.append(now.isoformat())
        state["welcome_recent_users"] = recent_users
        state["welcome_timestamps"] = timestamps[-20:]
        state["welcome_last_at"] = now.isoformat()
        state["welcome_count"] = int(state.get("welcome_count", 0)) + 1
        run.state_json = director.dumps(state)

    director.default_run_state = patched_default_state
    director.candidate_events = patched_candidates
    director.rule_decision = patched_rule_decision
    director.ai_director_decision = patched_ai_decision
    director.apply_decision_state = patched_apply_state
    director.is_viewer_join = is_viewer_join
    director.welcome_allowed = welcome_allowed
    director.welcome_decision = welcome_decision
    director._aliver_live_welcome_v1 = True
