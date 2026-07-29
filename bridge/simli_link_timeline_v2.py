from __future__ import annotations

from datetime import datetime
from typing import Any

from bridge import simli_link_diagnostics_v2 as diagnostics_v2


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _diff_ms(later: Any, earlier: Any) -> float | None:
    left = _parse_iso(later)
    right = _parse_iso(earlier)
    if left is None or right is None or left < right:
        return None
    return round((left - right).total_seconds() * 1000.0, 1)


def _first_after(
    events: list[dict[str, Any]],
    event_name: str,
    threshold: Any,
) -> dict[str, Any] | None:
    after = _parse_iso(threshold)
    for row in events:
        if row.get("event") != event_name:
            continue
        at = _parse_iso(row.get("at"))
        if at is not None and (after is None or at >= after):
            return row
    return None


def build_pipeline_timeline(runtime: Any) -> dict[str, Any]:
    state = getattr(runtime, "state", {}) or {}
    renderer = getattr(runtime, "renderer", None)
    events = [dict(row) for row in list(getattr(renderer, "_diag_events", ()) or ())]
    test_started_at = state.get("link_test_started_at")
    if test_started_at:
        input_at = state.get("link_test_input_at")
        sent_at = state.get("link_test_sent_at")
        event_threshold = input_at or test_started_at
    else:
        input_at = state.get("first_non_silent_input_at")
        sent_at = state.get("first_audio_sent_at")
        event_threshold = input_at or state.get("capture_started_at")

    first_voice = _first_after(events, "first_non_silent_audio", event_threshold)
    voice_at = (first_voice or {}).get("at")
    first_render = _first_after(events, "first_video_rendered", event_threshold)
    first_mouth = _first_after(events, "first_mouth_motion", voice_at or event_threshold)
    render_at = (first_render or {}).get("at")
    mouth_at = (first_mouth or {}).get("at")
    return {
        "test_id": state.get("link_test_id"),
        "test_started_at": test_started_at,
        "capture_started_at": state.get("capture_started_at"),
        "first_non_silent_input_at": input_at,
        "first_audio_sent_at": sent_at,
        "first_non_silent_return_audio_at": voice_at,
        "first_video_rendered_at": render_at,
        "first_mouth_motion_at": mouth_at,
        "input_to_send_ms": _diff_ms(sent_at, input_at),
        "input_to_return_audio_ms": _diff_ms(voice_at, input_at),
        "input_to_first_render_ms": _diff_ms(render_at, input_at),
        "return_audio_to_mouth_ms": _diff_ms(mouth_at, voice_at),
    }


def install_link_timeline_v2() -> None:
    monitor_class = diagnostics_v2.v1.SimliLinkMonitor
    if getattr(monitor_class, "_aliver_link_timeline_v2", False):
        return

    diagnostics_v2.build_pipeline_timeline = build_pipeline_timeline
    original_begin_test = monitor_class.begin_test

    def begin_test(monitor: Any) -> dict[str, Any]:
        renderer = getattr(monitor.runtime, "renderer", None)
        if renderer is not None:
            renderer._diag_previous_mouth_gray = None
            renderer._diag_previous_upper_gray = None
        return original_begin_test(monitor)

    monitor_class.begin_test = begin_test
    monitor_class._aliver_link_timeline_v2 = True
