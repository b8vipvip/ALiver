from __future__ import annotations

import asyncio
import time
from typing import Any

from bridge.runtime_diagnostics import event
from bridge.simli_session import classify_simli_failure, utc_iso

VOICE_THRESHOLD_DBFS = -50.0
DEFAULT_IDLE_ARM_SECONDS = 0.8
DEFAULT_TARGET_AUDIO_MS = 420
DEFAULT_TARGET_VIDEO_MS = 500


def trim_idle_media(
    renderer: Any,
    *,
    target_audio_ms: int = DEFAULT_TARGET_AUDIO_MS,
    target_video_ms: int = DEFAULT_TARGET_VIDEO_MS,
) -> dict[str, Any]:
    """Drop old idle media before a new GPT_OUT utterance starts.

    The trim runs before the new speech is sent to Simli, so the discarded media is
    the old idle/look-ahead stream rather than the new answer.
    """

    audio_queue = getattr(renderer, "_audio_queue", None)
    video_queue = getattr(renderer, "_video_queue", None)
    before_audio_seconds = float(getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0)
    target_audio_seconds = max(0.08, min(float(target_audio_ms) / 1000.0, 1.5))
    dropped_audio_chunks = 0

    if audio_queue is not None:
        while (
            getattr(audio_queue, "qsize", lambda: 0)() > 1
            and float(getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0)
            > target_audio_seconds
        ):
            try:
                chunk = audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            duration = float(getattr(chunk, "duration", 0.0) or 0.0)
            renderer._audio_buffer_seconds = max(
                0.0,
                float(getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0) - duration,
            )
            dropped_audio_chunks += 1

    metrics = getattr(renderer, "_metrics", {}) or {}
    tuning = getattr(renderer, "_tuning", {}) or {}
    source_fps = float(
        metrics.get("source_pts_fps")
        or metrics.get("target_video_fps")
        or tuning.get("target_fps")
        or 25.0
    )
    keep_video_frames = max(3, int(source_fps * max(0.12, target_video_ms / 1000.0)))
    dropped_video_frames = 0
    if video_queue is not None:
        while getattr(video_queue, "qsize", lambda: 0)() > keep_video_frames:
            try:
                video_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped_video_frames += 1

    if dropped_audio_chunks or dropped_video_frames:
        reanchor = getattr(renderer, "_tuning_reanchor", None)
        if callable(reanchor):
            reanchor()

    after_audio_seconds = float(getattr(renderer, "_audio_buffer_seconds", 0.0) or 0.0)
    trimmed_audio_ms = max(0.0, (before_audio_seconds - after_audio_seconds) * 1000.0)
    metrics["idle_trim_count"] = int(metrics.get("idle_trim_count") or 0) + int(
        bool(dropped_audio_chunks or dropped_video_frames)
    )
    metrics["idle_trim_audio_ms_total"] = round(
        float(metrics.get("idle_trim_audio_ms_total") or 0.0) + trimmed_audio_ms,
        1,
    )
    metrics["idle_trim_video_frames_total"] = int(
        metrics.get("idle_trim_video_frames_total") or 0
    ) + dropped_video_frames
    metrics["idle_trim_last_audio_ms"] = round(trimmed_audio_ms, 1)
    metrics["idle_trim_last_video_frames"] = dropped_video_frames
    metrics["idle_trim_post_audio_buffer_ms"] = round(after_audio_seconds * 1000.0, 1)
    renderer._metrics = metrics

    result = {
        "trimmed_audio_ms": round(trimmed_audio_ms, 1),
        "dropped_audio_chunks": dropped_audio_chunks,
        "dropped_video_frames": dropped_video_frames,
        "post_audio_buffer_ms": round(after_audio_seconds * 1000.0, 1),
        "post_video_queue_size": getattr(video_queue, "qsize", lambda: 0)(),
    }
    if dropped_audio_chunks or dropped_video_frames:
        event("simli_idle_media_trimmed", **result)
    return result


def install_simli_realtime_v2(renderer_class: type, runtime_class: type) -> None:
    if getattr(runtime_class, "_aliver_realtime_v2", False):
        return

    renderer_class.trim_idle_media = trim_idle_media
    original_enqueue = runtime_class._enqueue_audio

    def patched_enqueue(runtime: Any, data: bytes) -> None:
        state = runtime.state
        now = time.monotonic()
        dbfs = float(state.get("last_input_dbfs") or -96.0)
        threshold = float(runtime.config.get("audio_active_dbfs", VOICE_THRESHOLD_DBFS))
        active = dbfs >= threshold
        was_active = bool(state.get("_aliver_voice_active"))
        last_voice = float(state.get("_aliver_last_voice_monotonic") or 0.0)
        arm_seconds = max(
            0.25,
            min(float(runtime.config.get("idle_trim_arm_seconds", DEFAULT_IDLE_ARM_SECONDS)), 5.0),
        )

        if active:
            silent_for = now - last_voice if last_voice > 0 else arm_seconds + 1.0
            if state.get("link_test_started_at") and not state.get("link_test_input_at"):
                state["link_test_input_at"] = utc_iso()
                state["link_test_input_dbfs"] = round(dbfs, 2)
            if (
                not was_active
                and silent_for >= arm_seconds
                and bool(runtime.config.get("low_latency_idle_trim", True))
                and runtime.renderer is not None
            ):
                result = trim_idle_media(
                    runtime.renderer,
                    target_audio_ms=int(
                        runtime.config.get("idle_trim_target_audio_ms", DEFAULT_TARGET_AUDIO_MS)
                    ),
                    target_video_ms=int(
                        runtime.config.get("idle_trim_target_video_ms", DEFAULT_TARGET_VIDEO_MS)
                    ),
                )
                state["last_idle_trim"] = {"at": utc_iso(), **result}
            state["_aliver_voice_active"] = True
            state["_aliver_last_voice_monotonic"] = now
        elif was_active and last_voice > 0 and now - last_voice >= arm_seconds:
            state["_aliver_voice_active"] = False

        original_enqueue(runtime, data)

    async def patched_sender(runtime: Any) -> None:
        try:
            while not runtime.stop_flag.is_set():
                try:
                    data = await asyncio.wait_for(runtime.audio_queue.get(), timeout=0.5)
                except TimeoutError:
                    if runtime.renderer and runtime.renderer.stop_event.is_set():
                        runtime.stop_flag.set()
                    continue
                if not runtime.client:
                    continue
                await runtime.client.send(data)
                now = utc_iso()
                if not runtime.state.get("first_any_audio_sent_at"):
                    runtime.state["first_any_audio_sent_at"] = now
                if (
                    runtime.state.get("first_non_silent_input_at")
                    and not runtime.state.get("first_audio_sent_at")
                ):
                    runtime.state["first_audio_sent_at"] = now
                if (
                    runtime.state.get("link_test_input_at")
                    and not runtime.state.get("link_test_sent_at")
                ):
                    runtime.state["link_test_sent_at"] = now
                runtime.state["sent_chunks"] += 1
                runtime.state["sent_bytes"] += len(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = classify_simli_failure(
                exc,
                phase="streaming",
                diagnostics=dict(runtime.state.get("diagnostics") or {}),
            )
            runtime.state.update(
                {"status": "failed", "error": detail["message_zh"], "error_detail": detail}
            )
            runtime.stop_flag.set()

    runtime_class._enqueue_audio = patched_enqueue
    runtime_class._sender_loop = patched_sender
    renderer_class._aliver_realtime_v2 = True
    runtime_class._aliver_realtime_v2 = True
