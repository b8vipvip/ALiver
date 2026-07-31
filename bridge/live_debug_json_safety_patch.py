from __future__ import annotations

import math
from typing import Any

from bridge import douyin_visible_collector as collector

_ORIGINAL_STATUS: Any = None


def json_safe(value: Any) -> Any:
    """Return a strict-JSON-compatible copy of a Bridge status payload.

    httpx serializes request JSON with ``allow_nan=False``. Diagnostic scoring
    may legitimately use +/- infinity internally as a rejection sentinel, but
    those values must never escape into registration, heartbeat, or command
    payloads.
    """

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, set):
        return [json_safe(item) for item in value]
    return value


def install_live_debug_json_safety_patch() -> None:
    global _ORIGINAL_STATUS

    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_json_safe_status_patch", False):
        return

    _ORIGINAL_STATUS = manager.status

    def status(self: Any) -> dict[str, Any]:
        value = _ORIGINAL_STATUS(self)
        cleaned = json_safe(value)
        return cleaned if isinstance(cleaned, dict) else {"status": "invalid"}

    manager.status = status
    manager._aliver_json_safe_status_patch = True
