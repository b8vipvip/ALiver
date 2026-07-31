from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.live_welcome_patch import is_viewer_join, welcome_allowed, welcome_decision


def test_viewer_join_is_distinct_from_other_system_messages():
    join = SimpleNamespace(event_type="system", content="进入了直播间")
    follow = SimpleNamespace(event_type="follow", content="关注了直播间")
    fans = SimpleNamespace(event_type="system", content="加入了粉丝团")

    assert is_viewer_join(join) is True
    assert is_viewer_join(follow) is False
    assert is_viewer_join(fans) is False


def test_welcome_decision_mentions_nickname_and_uses_wave():
    event = SimpleNamespace(id="event-1", event_type="system", user_name="小雪", content="进入了直播间")

    decision = welcome_decision(event, {"welcome_avatar_action": "wave"})

    assert decision["decision_type"] == "reply"
    assert decision["event_id"] == "event-1"
    assert "小雪" in decision["instruction"]
    assert decision["avatar_action"] == "wave"
    assert decision["priority"] >= 75


def test_welcome_limits_repeat_viewer_and_global_burst():
    now = datetime(2026, 7, 31, 4, 0, tzinfo=timezone.utc)
    settings = {
        "welcome_viewers_enabled": True,
        "welcome_per_viewer_cooldown_seconds": 1800,
        "welcome_global_cooldown_seconds": 12,
        "welcome_max_per_minute": 3,
    }
    state = {
        "welcome_recent_users": {"小雪": (now - timedelta(minutes=2)).isoformat()},
        "welcome_last_at": (now - timedelta(seconds=30)).isoformat(),
        "welcome_timestamps": [],
    }

    allowed, reason = welcome_allowed(state, settings, "小雪", now=now)
    assert allowed is False
    assert "冷却" in reason

    allowed, _ = welcome_allowed(state, settings, "新观众", now=now)
    assert allowed is True

    state["welcome_last_at"] = (now - timedelta(seconds=2)).isoformat()
    allowed, reason = welcome_allowed(state, settings, "另一位观众", now=now)
    assert allowed is False
    assert "全局" in reason
