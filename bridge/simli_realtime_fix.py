from __future__ import annotations

import asyncio
import bisect
import ctypes
import time
from collections import deque
from typing import Any

from bridge import simli_diagnostics, simli_waveout
from bridge.runtime_diagnostics import event, exception


def fast_interpolate(samples: list[tuple[float, float]], at: float) -> float | None:
    """Interpolate without rebuilding a timestamp list for every sample lookup."""
    if not samples or at < samples[0][0] or at > samples[-1][0]:
        return None
    index = bisect.bisect_left(samples, (at, float("-inf")))
    if index == 0:
        return samples[0][1]
    if index >= len(samples):
        return samples[-1][1]
    before_time, before_value = samples[index - 1]
    after_time, after_value = samples[index]
    width = after_time - before_time
    if width <= 1e-9:
        return after_value
    ratio = (at - before_time) / width
    return before_value + (after_value - before_value) * ratio


def _recent(values: Any, limit: int = 300) -> list[Any]:
    try:
        rows = list(values)
    except TypeError:
        return []
    return rows[-limit:]


def lightweight_renderer_status(renderer: Any) -> dict[str, Any]:
    """Return realtime metrics only; never run the expensive correlation report."""
    values = dict(getattr(renderer, "_metrics", {}) or {})
    audio_queue = getattr(renderer, "_audio_queue", None)
    video_queue = getattr(renderer, "_video_queue", None)
    audio_playhead = getattr(renderer, "_audio_playhead", None)
    try:
        audio_clock = float(audio_playhead()) if callable(audio_playhead) else 0.0
    except Exception:
        audio_clock = 0.0

    source_fps = simli_diagnostics.median_fps(
        _recent(getattr(renderer, "_diag_video_pts_deltas", ()))
    )
    receive_fps = simli_diagnostics.median_fps(
        _recent(getattr(renderer, "_diag_video_arrival_deltas", ()))
    )
    render_fps_recent = simli_diagnostics.median_fps(
        _recent(getattr(renderer, "_diag_video_render_deltas", ()))
    )
    speed = simli_diagnostics.timeline_speed_ratio(
        _recent(getattr(renderer, "_diag_speed_samples", ()), 360)
    )
    cached_report = getattr(renderer, "_diag_last_report", None)
    if isinstance(cached_report, dict):
        report = dict(cached_report)
    else:
        report = {
            "conclusion_zh": "实时状态使用轻量快照；完整相关性分析仅在主动诊断时执行。",
            "estimated_lip_sync_offset_ms": None,
            "first_onset_offset_ms": None,
            "correlation_confidence": "insufficient",
        }

    audio_buffer_seconds = float(
        getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0
    )
    last_video_clock = float(getattr(renderer, "_last_video_clock", 0.0) or 0.0)
    values.update(
        {
            "audio_buffer_ms": round(audio_buffer_seconds * 1000, 1),
            "audio_queue_size": audio_queue.qsize() if audio_queue is not None else 0,
            "video_queue_size": video_queue.qsize() if video_queue is not None else 0,
            "audio_clock_seconds": round(audio_clock, 3),
            "video_clock_seconds": round(last_video_clock, 3),
            "video_clock_mode": (getattr(renderer, "_tuning", {}) or {}).get(
                "clock_mode", getattr(renderer, "_diag_clock_mode", "unknown")
            ),
            "source_pts_fps": source_fps,
            "receive_fps": receive_fps,
            "render_fps_recent": render_fps_recent,
            "video_playback_speed_ratio": speed,
            "objective_diagnostics": report,
            "status_mode": "lightweight_realtime",
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
    started = float(getattr(renderer, "_started_monotonic", time.monotonic()))
    elapsed = max(0.001, time.monotonic() - started)
    rendered = float(values.get("video_frames_rendered") or 0)
    values["render_fps"] = round(rendered / elapsed, 2)
    return values


def lightweight_runtime_status(runtime: Any) -> dict[str, Any]:
    """Single-pass runtime state used by control heartbeats and the session page."""
    values = dict(getattr(runtime, "state", {}) or {})
    renderer_task = getattr(runtime, "renderer_task", None)
    sender_task = getattr(runtime, "sender_task", None)
    capture_thread = getattr(runtime, "capture_thread", None)
    values["renderer_task_done"] = bool(renderer_task and renderer_task.done())
    values["sender_task_done"] = bool(sender_task and sender_task.done())
    values["capture_thread_alive"] = bool(capture_thread and capture_thread.is_alive())
    renderer = getattr(runtime, "renderer", None)
    if renderer is not None:
        renderer_status = lightweight_renderer_status(renderer)
        values["renderer"] = renderer_status
        values["av_sync"] = renderer_status
    return values


class BufferedWindowsWaveOutStream(simli_waveout.WindowsWaveOutStream):
    """Queue several waveOut buffers so small WebRTC frames play continuously."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_buffers: deque[tuple[Any, Any, int, float]] = deque()
        self._pending_seconds = 0.0
        self._max_pending_seconds = 0.18

    def get_output_latency(self) -> float:
        return 0.04

    def _cleanup_done_locked(self, *, force: bool = False) -> None:
        while self._pending_buffers:
            buffer, header, size, duration = self._pending_buffers[0]
            if not force and not (int(header.dwFlags) & simli_waveout.WHDR_DONE):
                break
            code = int(
                self._dll.waveOutUnprepareHeader(
                    self._handle,
                    ctypes.byref(header),
                    size,
                )
            )
            if code == simli_waveout.WAVERR_STILLPLAYING:
                if force:
                    time.sleep(0.001)
                break
            if code != simli_waveout.MMSYSERR_NOERROR:
                simli_waveout._check(code, "waveOutUnprepareHeader")
            self._pending_buffers.popleft()
            self._pending_seconds = max(0.0, self._pending_seconds - duration)
            del buffer

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if self._closed or not self._handle:
                raise RuntimeError("waveOut stream is closed.")
            block_align = self.channels * self.bits_per_sample // 8
            usable = len(data) - (len(data) % block_align)
            if usable <= 0:
                return
            duration = usable / float(self.sample_rate * block_align)
            deadline = time.monotonic() + 2.0
            while self._pending_seconds + duration > self._max_pending_seconds:
                self._cleanup_done_locked()
                if self._pending_seconds + duration <= self._max_pending_seconds:
                    break
                if time.monotonic() >= deadline:
                    event(
                        "simli_waveout_backpressure_timeout",
                        queued_ms=round(self._pending_seconds * 1000, 1),
                    )
                    break
                time.sleep(0.001)

            buffer = ctypes.create_string_buffer(data[:usable])
            header = simli_waveout.WAVEHDR(
                lpData=ctypes.cast(buffer, ctypes.c_void_p),
                dwBufferLength=usable,
                dwBytesRecorded=0,
                dwUser=0,
                dwFlags=0,
                dwLoops=0,
                lpNext=None,
                reserved=0,
            )
            size = ctypes.sizeof(header)
            simli_waveout._check(
                int(
                    self._dll.waveOutPrepareHeader(
                        self._handle,
                        ctypes.byref(header),
                        size,
                    )
                ),
                "waveOutPrepareHeader",
            )
            try:
                simli_waveout._check(
                    int(
                        self._dll.waveOutWrite(
                            self._handle,
                            ctypes.byref(header),
                            size,
                        )
                    ),
                    "waveOutWrite",
                )
            except Exception:
                self._dll.waveOutUnprepareHeader(
                    self._handle,
                    ctypes.byref(header),
                    size,
                )
                raise
            self._pending_buffers.append((buffer, header, size, duration))
            self._pending_seconds += duration
            self._cleanup_done_locked()

    def _drain_after_reset_locked(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while self._pending_buffers and time.monotonic() < deadline:
            self._cleanup_done_locked(force=True)
            if self._pending_buffers:
                time.sleep(0.001)
        if self._pending_buffers:
            event(
                "simli_waveout_cleanup_timeout",
                remaining_buffers=len(self._pending_buffers),
            )

    def stop_stream(self) -> None:
        with self._lock:
            if self._closed or not self._handle:
                return
            self._dll.waveOutReset(self._handle)
            self._drain_after_reset_locked()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._handle:
                self._dll.waveOutReset(self._handle)
                self._drain_after_reset_locked()
                self._dll.waveOutClose(self._handle)
                self._handle = ctypes.c_void_p()
            self._closed = True


async def fast_renderer_close(renderer: Any) -> None:
    """Release renderer resources without running a full diagnostic correlation pass."""
    if renderer.stop_event.is_set() and renderer._metrics.get("status") == "ended":
        return
    event("simli_renderer_fast_close_enter")
    renderer.stop_event.set()
    current = asyncio.current_task()
    tasks = list(getattr(renderer, "_tasks", ()))
    for task in tasks:
        if task is not current and not task.done():
            task.cancel()
    pending = [task for task in tasks if task is not current]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    audio_stream = getattr(renderer, "_audio_stream", None)
    if audio_stream is not None:
        try:
            await asyncio.to_thread(audio_stream.stop_stream)
        except Exception as exc:
            exception("simli_fast_close_audio_stop_failed", exc)
        try:
            await asyncio.to_thread(audio_stream.close)
        except Exception as exc:
            exception("simli_fast_close_audio_close_failed", exc)
        renderer._audio_stream = None

    audio = getattr(renderer, "_audio", None)
    if audio is not None:
        try:
            await asyncio.to_thread(audio.terminate)
        except Exception as exc:
            exception("simli_fast_close_audio_terminate_failed", exc)
        renderer._audio = None

    try:
        renderer.cv2.destroyWindow(renderer.window_title)
    except Exception:
        pass
    renderer._metrics["status"] = "ended"
    event("simli_renderer_fast_close_exit")


def install_simli_realtime_fix(renderer_class: type, runtime_class: type) -> None:
    if getattr(renderer_class, "_aliver_realtime_fix_v1", False):
        return

    # The original interpolation rebuilt a full timestamp list on every lookup,
    # turning status/close diagnostics into multi-second (eventually minute-long) stalls.
    simli_diagnostics.interpolate = fast_interpolate

    # The waveOut implementation waited for every tiny buffer to finish before submitting
    # the next one. Queueing a short rolling buffer removes the audible gaps / ~0.5x effect.
    simli_waveout.WindowsWaveOutStream = BufferedWindowsWaveOutStream

    original_init = renderer_class.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        tuning = getattr(self, "_tuning", {}) or {}
        if tuning.get("clock_mode"):
            self._diag_clock_mode = str(tuning["clock_mode"])
        self._metrics["realtime_fix"] = "v1"

    renderer_class.__init__ = patched_init
    renderer_class.status = lightweight_renderer_status
    renderer_class.close = fast_renderer_close
    runtime_class.status = lightweight_runtime_status
    renderer_class._aliver_realtime_fix_v1 = True
    runtime_class._aliver_realtime_fix_v1 = True
