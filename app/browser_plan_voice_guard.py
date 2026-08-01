from __future__ import annotations

from typing import Any

from app import voice_service
from app.browser_director_plan_service import is_browser_plan_active


def _looks_like_director_plan(text: str) -> bool:
    compact = text.strip()
    if not compact:
        return False
    if '"aliver_plan_request_id"' in compact:
        return True
    required = ('"director_name"', '"show_title"', '"rundown"', '"opening_script"')
    return compact.startswith("{") and all(item in compact for item in required)


def install_browser_plan_voice_guard() -> None:
    if getattr(voice_service, "_aliver_browser_plan_voice_guard", False):
        return
    original = voice_service.handle_assistant_completed

    async def guarded_handle_assistant_completed(extension_id: str, data: dict[str, Any]) -> None:
        text = str(data.get("text") or "")
        if is_browser_plan_active(extension_id) or _looks_like_director_plan(text):
            return
        await original(extension_id, data)

    voice_service.handle_assistant_completed = guarded_handle_assistant_completed
    voice_service._aliver_browser_plan_voice_guard = True
