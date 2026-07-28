from __future__ import annotations

import time
from typing import Any

from bridge.runtime_diagnostics import event
from bridge.simli_tuning import PROFILE_PATH, load_tuning_profile


def _find_runtime(manager: Any, session_id: str | None = None) -> Any | None:
    sessions = getattr(manager, "sessions", {})
    if session_id and session_id in sessions:
        return sessions[session_id]
    for runtime in sessions.values():
        if runtime.state.get("status") in {"active", "starting"}:
            return runtime
    return None


def _renderer_fast_status(renderer: Any) -> dict[str, Any]:
    metrics = dict(getattr(renderer, "_metrics", {}) or {})
    audio_buffer_seconds = float(getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0)
    audio_queue = getattr(renderer, "_audio_queue", None)
    video_queue = getattr(renderer, "_video_queue", None)
    audio_playhead = getattr(renderer, "_audio_playhead", None)
    try:
        audio_clock = float(audio_playhead()) if callable(audio_playhead) else 0.0
    except Exception:
        audio_clock = 0.0
    metrics.update(
        {
            "audio_buffer_ms": round(audio_buffer_seconds * 1000, 1),
            "audio_queue_size": audio_queue.qsize() if audio_queue is not None else 0,
            "video_queue_size": video_queue.qsize() if video_queue is not None else 0,
            "audio_clock_seconds": round(audio_clock, 3),
            "video_clock_seconds": round(float(getattr(renderer, "_last_video_clock", 0.0) or 0.0), 3),
            "status_source": "lightweight_snapshot",
        }
    )
    offset = abs(float(metrics.get("av_offset_ms") or 0.0))
    if metrics.get("status") != "active":
        metrics["sync_health"] = metrics.get("status", "starting")
    elif offset <= 80:
        metrics["sync_health"] = "good"
    elif offset <= 200:
        metrics["sync_health"] = "warning"
    else:
        metrics["sync_health"] = "bad"
    return metrics


def fast_manager_tuning_status(manager: Any, *, session_id: str | None = None) -> dict[str, Any]:
    started = time.monotonic()
    runtime = _find_runtime(manager, session_id)
    if runtime is None:
        profile = load_tuning_profile()
        result = {
            "session_active": False,
            "session_id": None,
            "settings": profile,
            "profile_path": str(PROFILE_PATH.resolve()),
            "profile_exists": PROFILE_PATH.exists(),
            "latest_test": None,
            "av_sync": {"status_source": "no_active_session"},
        }
    else:
        renderer = getattr(runtime, "renderer", None)
        if renderer is None or not hasattr(renderer, "_tuning_snapshot"):
            raise RuntimeError("Simli 调参器尚未初始化。")
        result = renderer._tuning_snapshot()
        result.update(
            {
                "session_active": runtime.state.get("status") == "active",
                "session_id": runtime.session_id,
                "session_status": runtime.state.get("status"),
                "av_sync": _renderer_fast_status(renderer),
            }
        )
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    if elapsed_ms >= 100:
        event(
            "simli_tuning_status_slow",
            session_id=session_id,
            elapsed_ms=elapsed_ms,
        )
    return result
