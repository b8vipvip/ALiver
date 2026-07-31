from __future__ import annotations

from typing import Any

JOIN_MARKERS = ("进入了直播间", "进入直播间", "来了")


def is_join_payload(event_type: str, content: str) -> bool:
    return str(event_type or "").lower() == "system" and any(
        marker in str(content or "") for marker in JOIN_MARKERS
    )


def install_live_welcome_ingest_patch() -> None:
    # Imported here, after pro_director_runtime_patch has installed its function
    # wrappers. douyin_live_service imports score_event later and therefore sees
    # this final implementation.
    from app import auto_director_service

    if getattr(auto_director_service, "_aliver_join_score_v1", False):
        return
    original_score_event = auto_director_service.score_event

    def patched_score_event(
        event_type: str,
        user_name: str,
        content: str,
        settings: dict[str, Any],
    ) -> tuple[str, int, str]:
        if is_join_payload(event_type, content):
            nickname = " ".join(str(user_name or "").split()).strip()
            if not nickname or nickname in {"观众", "用户", "匿名", "匿名观众"}:
                return "ignored", 0, "进入通知没有可称呼的观众昵称"
            # Viewer-entry events must reach the director even when the user has
            # raised min_score above the old generic system-event score of 40.
            return "queued", 82, "识别到带昵称的新观众进入通知，交由欢迎限频逻辑处理"
        return original_score_event(event_type, user_name, content, settings)

    auto_director_service.score_event = patched_score_event
    auto_director_service._aliver_join_score_v1 = True
