from __future__ import annotations

import asyncio
import time
from typing import Any

from bridge.runtime_diagnostics import event, exception
from bridge.simli_sync import DEFAULT_VIDEO_FPS


def _safe_gray(region: Any) -> Any:
    """Create a small contiguous luminance array without OpenCV slice conversions."""
    import numpy as np

    sampled = np.asarray(region)[::4, ::4, :3]
    rgb = sampled.astype(np.float32, copy=True)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def install_simli_crash_guard(renderer_class: type) -> None:
    if getattr(renderer_class, "_aliver_crash_guard_v1", False):
        return

    original_init = renderer_class.__init__
    original_open_window = renderer_class._open_window
    original_open_audio = renderer_class._open_audio_output
    original_close = renderer_class.close

    def patched_init(self, *args, **kwargs):
        event("simli_renderer_init_enter")
        original_init(self, *args, **kwargs)
        self._guard_frame_count = 0
        self._guard_last_motion = 0.0
        self._guard_last_threshold = 1.2
        self._guard_analysis_every = 2
        self._guard_analysis_after = 12
        event(
            "simli_renderer_init_ok",
            window_title=self.window_title,
            window_size=self.window_size,
            play_return_audio=self.play_return_audio,
            audio_output_device_index=self.audio_output_device_index,
        )

    def patched_open_window(self):
        event("simli_window_open_enter", title=self.window_title, size=self.window_size)
        cv2 = self.cv2
        try:
            cv2.setNumThreads(1)
        except Exception:
            pass
        try:
            if hasattr(cv2, "ocl"):
                cv2.ocl.setUseOpenCL(False)
        except Exception:
            pass
        original_open_window(self)
        event("simli_window_open_ok")

    def patched_open_audio(self):
        event("simli_audio_output_open_enter")
        original_open_audio(self)
        event(
            "simli_audio_output_open_ok",
            output_device=self._metrics.get("audio_output_device"),
            output_device_index=self._metrics.get("audio_output_device_index"),
            output_latency_ms=self._metrics.get("audio_output_latency_ms"),
        )

    def safe_mouth_motion(self, image: Any) -> tuple[float, float]:
        frame_number = int(getattr(self, "_guard_frame_count", 0))
        if frame_number < self._guard_analysis_after or frame_number % self._guard_analysis_every:
            return self._guard_last_motion, self._guard_last_threshold
        height, width = image.shape[:2]
        mouth = image[int(height * 0.42) : int(height * 0.72), int(width * 0.25) : int(width * 0.75)]
        upper = image[int(height * 0.16) : int(height * 0.42), int(width * 0.25) : int(width * 0.75)]
        mouth_gray = _safe_gray(mouth)
        upper_gray = _safe_gray(upper)
        previous_mouth = getattr(self, "_diag_previous_mouth_gray", None)
        previous_upper = getattr(self, "_diag_previous_upper_gray", None)
        if previous_mouth is None or previous_upper is None:
            score = 0.0
        else:
            import numpy as np

            mouth_change = float(np.mean(np.abs(mouth_gray - previous_mouth)))
            upper_change = float(np.mean(np.abs(upper_gray - previous_upper)))
            score = max(0.0, mouth_change - 0.55 * upper_change)
        self._diag_previous_mouth_gray = mouth_gray
        self._diag_previous_upper_gray = upper_gray
        if getattr(self, "_diag_first_audio_onset", None) is None:
            self._diag_motion_baseline.append(score)
        import statistics

        baseline = statistics.median(self._diag_motion_baseline) if self._diag_motion_baseline else 0.0
        threshold = max(1.2, baseline * 2.4 + 0.35)
        self._guard_last_motion = round(score, 4)
        self._guard_last_threshold = round(threshold, 4)
        return self._guard_last_motion, self._guard_last_threshold

    async def safe_display_video(self) -> None:
        await self._audio_started.wait()
        cv2 = self.cv2
        event("simli_video_display_loop_started")
        while not self.stop_event.is_set():
            try:
                packet = await asyncio.wait_for(self._video_queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            target = packet.sequence / DEFAULT_VIDEO_FPS + self.video_delay_seconds
            while not self.stop_event.is_set():
                playhead = self._audio_playhead()
                delta = target - playhead
                if delta <= 0.003:
                    break
                await asyncio.sleep(min(delta, 0.03))
            playhead = self._audio_playhead()
            lateness = playhead - target
            if lateness > self.late_drop_seconds and not self._video_queue.empty():
                self._metrics["video_frames_dropped"] += 1
                continue

            frame_number = self._guard_frame_count
            trace_frame = frame_number < 4
            if trace_frame:
                event("simli_frame_to_ndarray_enter", frame_number=frame_number, sequence=packet.sequence)
            image = packet.frame.to_ndarray()
            if trace_frame:
                event(
                    "simli_frame_to_ndarray_ok",
                    frame_number=frame_number,
                    shape=list(getattr(image, "shape", ())),
                    dtype=str(getattr(image, "dtype", "unknown")),
                    contiguous=bool(getattr(getattr(image, "flags", None), "c_contiguous", False)),
                )

            # NumPy channel reversal avoids an additional cv2.cvtColor native call before imshow.
            if trace_frame:
                event("simli_frame_bgr_copy_enter", frame_number=frame_number)
            bgr = image[..., ::-1].copy()
            if trace_frame:
                event("simli_frame_bgr_copy_ok", frame_number=frame_number)
                event("simli_cv2_imshow_enter", frame_number=frame_number)
            cv2.imshow(self.window_title, bgr)
            if trace_frame:
                event("simli_cv2_imshow_ok", frame_number=frame_number)
                event("simli_cv2_waitkey_enter", frame_number=frame_number)
            cv2.waitKey(1)
            if trace_frame:
                event("simli_cv2_waitkey_ok", frame_number=frame_number)

            motion, threshold = self._diag_mouth_motion(image)
            now = time.monotonic()
            wall_elapsed = now - (self._audio_start_monotonic or now)
            if self._diag_previous_video_render is not None:
                self._diag_video_render_deltas.append(now - self._diag_previous_video_render)
            self._diag_previous_video_render = now
            self._diag_video_samples.append((target, motion))
            self._diag_speed_samples.append((wall_elapsed, target))
            if self._diag_first_video_render_clock is None:
                self._diag_first_video_render_clock = target
                self._diag_record_event(
                    "first_video_rendered",
                    video_clock_ms=round(target * 1000, 1),
                    audio_playhead_ms=round(playhead * 1000, 1),
                )
                event("simli_first_video_rendered", video_clock_ms=round(target * 1000, 1))
            if self._diag_first_mouth_onset is None and motion >= threshold:
                self._diag_first_mouth_onset = target
                self._diag_record_event(
                    "first_mouth_motion",
                    video_clock_ms=round(target * 1000, 1),
                    motion_score=motion,
                    motion_threshold=threshold,
                )
            self._last_video_clock = target
            self._metrics["video_frames_rendered"] += 1
            self._metrics["scheduler_lateness_ms"] = round(lateness * 1000, 1)
            self._guard_frame_count += 1
            try:
                if cv2.getWindowProperty(self.window_title, cv2.WND_PROP_VISIBLE) < 1:
                    self.stop_event.set()
                    break
            except Exception:
                pass
            await asyncio.sleep(0)

    async def patched_close(self):
        event("simli_renderer_close_enter")
        try:
            return await original_close(self)
        except Exception as exc:
            exception("simli_renderer_close_failed", exc)
            raise
        finally:
            event("simli_renderer_close_exit")

    renderer_class.__init__ = patched_init
    renderer_class._open_window = patched_open_window
    renderer_class._open_audio_output = patched_open_audio
    renderer_class._diag_mouth_motion = safe_mouth_motion
    renderer_class._display_video = safe_display_video
    renderer_class.close = patched_close
    renderer_class._aliver_crash_guard_v1 = True


def install_simli_runtime_guard(runtime_class: type) -> None:
    if getattr(runtime_class, "_aliver_runtime_guard_v1", False):
        return
    original_set_phase = runtime_class._set_phase
    original_start = runtime_class.start
    original_stop = runtime_class.stop

    def patched_set_phase(self, phase: str) -> None:
        event(
            "simli_phase_enter",
            session_id=self.session_id,
            phase=phase,
            previous_phase=self.state.get("phase"),
        )
        original_set_phase(self, phase)

    async def patched_start(self):
        event("simli_runtime_start_enter", session_id=self.session_id)
        try:
            result = await original_start(self)
        except Exception as exc:
            exception(
                "simli_runtime_start_failed",
                exc,
                session_id=self.session_id,
                phase=self.state.get("phase"),
                state=self.state,
            )
            raise
        event("simli_runtime_start_ok", session_id=self.session_id, state=self.state)
        for task_name in ("renderer_task", "sender_task"):
            task = getattr(self, task_name, None)
            if task is None:
                continue

            def done_callback(done_task, *, name=task_name, session_id=self.session_id):
                if done_task.cancelled():
                    event("simli_background_task_cancelled", session_id=session_id, task=name)
                    return
                error = done_task.exception()
                if error is None:
                    event("simli_background_task_completed", session_id=session_id, task=name)
                else:
                    exception("simli_background_task_failed", error, session_id=session_id, task=name)

            task.add_done_callback(done_callback)
        return result

    async def patched_stop(self):
        event("simli_runtime_stop_enter", session_id=self.session_id, state=self.state)
        try:
            return await original_stop(self)
        finally:
            event("simli_runtime_stop_exit", session_id=self.session_id, state=self.state)

    runtime_class._set_phase = patched_set_phase
    runtime_class.start = patched_start
    runtime_class.stop = patched_stop
    runtime_class._aliver_runtime_guard_v1 = True
