from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any


def _tail(values: Iterable[Any], limit: int = 180) -> list[Any]:
    rows = list(values)
    return rows[-limit:]


def _median_fps(deltas: Iterable[float]) -> float | None:
    rows = sorted(float(value) for value in _tail(deltas, 180) if 0 < float(value) < 2)
    if not rows:
        return None
    middle = len(rows) // 2
    median = rows[middle] if len(rows) % 2 else (rows[middle - 1] + rows[middle]) / 2
    return round(1.0 / median, 3) if median > 0 else None


def _timeline_speed_ratio(samples: Iterable[tuple[float, float]]) -> float | None:
    rows = _tail(samples, 180)
    if len(rows) < 2:
        return None
    wall_span = float(rows[-1][0]) - float(rows[0][0])
    media_span = float(rows[-1][1]) - float(rows[0][1])
    if wall_span <= 0.05 or media_span < 0:
        return None
    return round(media_span / wall_span, 4)


def _lightweight_report(renderer: Any) -> dict[str, Any]:
    cached = getattr(renderer, "_diag_last_report", None)
    if isinstance(cached, dict):
        return dict(cached)
    latest = getattr(renderer, "_tuning_latest_report", None)
    if isinstance(latest, dict):
        return dict(latest)
    return {
        "conclusion_zh": "尚未执行完整音画同步测试。实时状态使用轻量快照，不会阻塞 Bridge。",
        "correlation_confidence": "insufficient",
        "estimated_lip_sync_offset_ms": None,
        "first_onset_offset_ms": None,
    }


def fast_status_snapshot(renderer: Any) -> dict[str, Any]:
    started = time.perf_counter()
    values = dict(getattr(renderer, "_metrics", {}) or {})
    audio_queue = getattr(renderer, "_audio_queue", None)
    video_queue = getattr(renderer, "_video_queue", None)
    audio_playhead = getattr(renderer, "_audio_playhead", None)
    values.update(
        {
            "audio_buffer_ms": round(float(getattr(renderer, "_audio_buffer_seconds", 0.0)) * 1000, 1),
            "audio_queue_size": audio_queue.qsize() if audio_queue is not None else 0,
            "video_queue_size": video_queue.qsize() if video_queue is not None else 0,
            "audio_clock_seconds": round(float(audio_playhead()), 3) if callable(audio_playhead) else 0.0,
            "video_clock_seconds": round(float(getattr(renderer, "_last_video_clock", 0.0)), 3),
            "source_pts_fps": _median_fps(getattr(renderer, "_diag_video_pts_deltas", ())),
            "receive_fps": _median_fps(getattr(renderer, "_diag_video_arrival_deltas", ())),
            "render_fps_recent": _median_fps(getattr(renderer, "_diag_video_render_deltas", ())),
            "video_playback_speed_ratio": _timeline_speed_ratio(
                getattr(renderer, "_diag_speed_samples", ())
            ),
            "objective_diagnostics": _lightweight_report(renderer),
            "status_mode": "lightweight_snapshot",
        }
    )
    tuning_snapshot = getattr(renderer, "_tuning_snapshot", None)
    if callable(tuning_snapshot):
        values["tuning"] = tuning_snapshot()
    offset = abs(float(values.get("av_offset_ms") or 0.0))
    if values.get("status") != "active":
        values["sync_health"] = values.get("status", "starting")
    elif offset <= 80:
        values["sync_health"] = "good"
    elif offset <= 200:
        values["sync_health"] = "warning"
    else:
        values["sync_health"] = "bad"
    renderer_started = float(getattr(renderer, "_started_monotonic", time.monotonic()))
    elapsed = max(0.001, time.monotonic() - renderer_started)
    values["render_fps"] = round(float(values.get("video_frames_rendered") or 0) / elapsed, 2)
    values["status_snapshot_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return values


def install_simli_fast_status(renderer_class: type) -> None:
    if getattr(renderer_class, "_aliver_fast_status_v1", False):
        return

    def patched_status(self) -> dict[str, Any]:
        return fast_status_snapshot(self)

    renderer_class.status = patched_status
    renderer_class._aliver_fast_status_v1 = True
