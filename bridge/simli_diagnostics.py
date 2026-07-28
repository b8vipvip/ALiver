from __future__ import annotations

import asyncio
import bisect
import json
import math
import statistics
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from bridge.audio_capture import calculate_pcm16_levels
    from bridge.simli_sync import (
        AUDIO_RATE,
        DEFAULT_VIDEO_FPS,
        VideoPacket,
        _CONFIG,
        frame_time_seconds,
    )
except ModuleNotFoundError:
    from audio_capture import calculate_pcm16_levels
    from simli_sync import AUDIO_RATE, DEFAULT_VIDEO_FPS, VideoPacket, _CONFIG, frame_time_seconds

DIAGNOSTICS_DIR = Path(__file__).resolve().parent / "diagnostics"
DIAGNOSTIC_VERSION = 2
AUDIO_ACTIVE_DBFS = -50.0
GRID_SECONDS = 0.05
MAX_LAG_SECONDS = 2.0


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def median_fps(deltas: list[float] | deque[float]) -> float | None:
    values = [float(value) for value in deltas if 0.001 <= float(value) <= 1.0]
    if not values:
        return None
    median = statistics.median(values)
    return round(1.0 / median, 3) if median > 0 else None


def pearson_correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    centered_left = [value - mean_left for value in left]
    centered_right = [value - mean_right for value in right]
    variance_left = sum(value * value for value in centered_left)
    variance_right = sum(value * value for value in centered_right)
    if variance_left <= 1e-9 or variance_right <= 1e-9:
        return None
    numerator = sum(a * b for a, b in zip(centered_left, centered_right, strict=True))
    return numerator / math.sqrt(variance_left * variance_right)


def interpolate(samples: list[tuple[float, float]], at: float) -> float | None:
    if not samples or at < samples[0][0] or at > samples[-1][0]:
        return None
    times = [row[0] for row in samples]
    index = bisect.bisect_left(times, at)
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


def estimate_signal_lag(
    audio_samples: list[tuple[float, float]],
    video_samples: list[tuple[float, float]],
    *,
    max_lag_seconds: float = MAX_LAG_SECONDS,
    step_seconds: float = GRID_SECONDS,
) -> dict[str, Any]:
    """Estimate lip delay. Positive lag means mouth motion trails audible speech."""
    if len(audio_samples) < 20 or len(video_samples) < 20:
        return {"lag_ms": None, "correlation": None, "points": 0, "confidence": "insufficient"}

    audio_samples = sorted(audio_samples)
    video_samples = sorted(video_samples)
    best: tuple[float, float, int] | None = None
    steps = int(round(max_lag_seconds / step_seconds))
    for offset_index in range(-steps, steps + 1):
        lag = offset_index * step_seconds
        start = max(audio_samples[0][0], video_samples[0][0] - lag)
        end = min(audio_samples[-1][0], video_samples[-1][0] - lag)
        if end - start < 3.0:
            continue
        left: list[float] = []
        right: list[float] = []
        cursor = start
        while cursor <= end:
            audio_value = interpolate(audio_samples, cursor)
            video_value = interpolate(video_samples, cursor + lag)
            if audio_value is not None and video_value is not None:
                left.append(audio_value)
                right.append(video_value)
            cursor += step_seconds
        correlation = pearson_correlation(left, right)
        if correlation is None:
            continue
        if best is None or correlation > best[1]:
            best = (lag, correlation, len(left))

    if best is None:
        return {"lag_ms": None, "correlation": None, "points": 0, "confidence": "insufficient"}
    lag, correlation, points = best
    if correlation >= 0.45 and points >= 120:
        confidence = "high"
    elif correlation >= 0.25 and points >= 80:
        confidence = "medium"
    elif correlation >= 0.12:
        confidence = "low"
    else:
        confidence = "insufficient"
    return {
        "lag_ms": round(lag * 1000, 1),
        "correlation": round(correlation, 4),
        "points": points,
        "confidence": confidence,
    }


def timeline_speed_ratio(samples: list[tuple[float, float]]) -> float | None:
    if len(samples) < 2:
        return None
    start_wall, start_clock = samples[0]
    end_wall, end_clock = samples[-1]
    wall_delta = end_wall - start_wall
    clock_delta = end_clock - start_clock
    if wall_delta < 1.0 or clock_delta < 0:
        return None
    return round(clock_delta / wall_delta, 4)


def sanitize_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return cleaned[:100] or "unknown-session"


def _find_onset(samples: list[tuple[float, float]], threshold: float) -> float | None:
    for at, value in samples:
        if value >= threshold:
            return at
    return None


def build_diagnostic_conclusion(report: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    problems: list[str] = []
    suggestions: list[str] = []
    onset = report.get("first_onset_offset_ms")
    lag = report.get("estimated_lip_sync_offset_ms")
    confidence = report.get("correlation_confidence")
    speed = report.get("video_playback_speed_ratio")
    source_fps = report.get("source_pts_fps")

    if onset is not None:
        if onset > 250:
            problems.append(f"首次明显口型比声音晚 {onset:.0f} ms")
        elif onset < -250:
            problems.append(f"首次明显口型比声音早 {abs(onset):.0f} ms")
    if lag is not None and confidence in {"medium", "high"}:
        if lag > 180:
            problems.append(f"持续口型平均比声音晚约 {lag:.0f} ms")
        elif lag < -180:
            problems.append(f"持续口型平均比声音早约 {abs(lag):.0f} ms")
    if speed is not None:
        if speed < 0.85:
            problems.append(f"视频时间轴只有实时速度的 {speed:.2f} 倍，存在慢放")
        elif speed > 1.15:
            problems.append(f"视频时间轴达到实时速度的 {speed:.2f} 倍，存在快放")
    if source_fps is not None and not 24 <= source_fps <= 36:
        problems.append(f"Simli 视频 PTS 推算为 {source_fps:.2f} FPS，与标准 30 FPS 不一致")

    if onset is not None or lag is not None:
        measured = lag if lag is not None and confidence in {"medium", "high"} else onset
        if measured is not None and abs(measured) > 120:
            correction = int(max(-500, min(500, -measured)))
            suggestions.append(f"可将 video_delay_ms 在当前值基础上调整 {correction:+d} ms")
    if speed is not None and speed < 0.85:
        suggestions.append("保持 fixed_30fps 时钟，不再直接使用异常的跨轨 PTS 播放速度")
    if confidence in {"low", "insufficient"}:
        suggestions.append("检测窗口内请让数字人连续说 8～12 秒，避免只有静音或极短语句")

    if not problems:
        if confidence in {"medium", "high"}:
            conclusion = "数据判断音画同步正常，未发现明显慢放。"
        else:
            conclusion = "未发现明确异常，但有效语音或口部运动不足，结论置信度有限。"
    else:
        conclusion = "；".join(problems) + "。"
    return conclusion, problems, suggestions


def install_simli_diagnostics_patch(renderer_class: type) -> None:
    if getattr(renderer_class, "_aliver_diagnostics_v2", False):
        return

    original_init = renderer_class.__init__
    original_close = renderer_class.close

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        config = dict(_CONFIG.get() or {})
        self._diag_session_id = str(config.get("_session_id") or "unknown-session")
        self._diag_clock_mode = "fixed_30fps"
        self._diag_events: deque[dict[str, Any]] = deque(maxlen=500)
        self._diag_audio_samples: deque[tuple[float, float]] = deque(maxlen=7200)
        self._diag_audio_dbfs: deque[tuple[float, float]] = deque(maxlen=7200)
        self._diag_video_samples: deque[tuple[float, float]] = deque(maxlen=3600)
        self._diag_speed_samples: deque[tuple[float, float]] = deque(maxlen=3600)
        self._diag_video_pts_deltas: deque[float] = deque(maxlen=300)
        self._diag_video_arrival_deltas: deque[float] = deque(maxlen=300)
        self._diag_video_render_deltas: deque[float] = deque(maxlen=300)
        self._diag_previous_video_pts = None
        self._diag_previous_video_arrival = None
        self._diag_previous_video_render = None
        self._diag_previous_mouth_gray = None
        self._diag_previous_upper_gray = None
        self._diag_motion_baseline: deque[float] = deque(maxlen=45)
        self._diag_first_audio_onset = None
        self._diag_first_mouth_onset = None
        self._diag_first_video_render_clock = None
        self._diag_last_snapshot = 0.0
        self._diag_last_report: dict[str, Any] | None = None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"simli-{sanitize_filename(self._diag_session_id)}-{stamp}"
        self._diag_event_path = DIAGNOSTICS_DIR / f"{filename}.jsonl"
        self._diag_report_path = DIAGNOSTICS_DIR / f"{filename}.report.json"
        self._diag_record_event(
            "renderer_initialized",
            clock_mode=self._diag_clock_mode,
            expected_video_fps=DEFAULT_VIDEO_FPS,
            configured_prebuffer_ms=round(self.prebuffer_seconds * 1000),
        )

    def diag_record_event(self, event: str, **details: Any) -> None:
        row = {
            "event": event,
            "at": utc_iso(),
            "elapsed_ms": round((time.monotonic() - self._started_monotonic) * 1000, 1),
            **details,
        }
        self._diag_events.append(row)
        try:
            DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
            with self._diag_event_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def diag_mouth_motion(self, image: Any) -> tuple[float, float]:
        cv2 = self.cv2
        height, width = image.shape[:2]
        mouth = image[int(height * 0.42) : int(height * 0.72), int(width * 0.25) : int(width * 0.75)]
        upper = image[int(height * 0.16) : int(height * 0.42), int(width * 0.25) : int(width * 0.75)]
        mouth_gray = cv2.cvtColor(mouth, cv2.COLOR_RGB2GRAY)
        upper_gray = cv2.cvtColor(upper, cv2.COLOR_RGB2GRAY)
        if self._diag_previous_mouth_gray is None:
            score = 0.0
        else:
            mouth_change = float(cv2.mean(cv2.absdiff(mouth_gray, self._diag_previous_mouth_gray))[0])
            upper_change = float(cv2.mean(cv2.absdiff(upper_gray, self._diag_previous_upper_gray))[0])
            score = max(0.0, mouth_change - 0.55 * upper_change)
        self._diag_previous_mouth_gray = mouth_gray
        self._diag_previous_upper_gray = upper_gray
        if self._diag_first_audio_onset is None:
            self._diag_motion_baseline.append(score)
        baseline = statistics.median(self._diag_motion_baseline) if self._diag_motion_baseline else 0.0
        threshold = max(1.2, baseline * 2.4 + 0.35)
        return round(score, 4), round(threshold, 4)

    async def patched_wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 8.0
        minimum_video_frames = max(4, int(DEFAULT_VIDEO_FPS * min(self.prebuffer_seconds, 0.35)))
        while not self.stop_event.is_set():
            tracks_ready = self._audio_ready.is_set() and self._video_ready.is_set()
            enough_video = self._video_queue.qsize() >= minimum_video_frames
            if tracks_ready and enough_video and self._audio_buffer_seconds >= self.prebuffer_seconds:
                break
            if time.monotonic() >= deadline:
                if not tracks_ready:
                    raise RuntimeError("等待 Simli 音视频轨超时，未同时收到音频和视频。")
                break
            await asyncio.sleep(0.02)
        self._timeline_start_delta = 0.0
        self._metrics["timeline_start_delta_ms"] = 0.0
        self._metrics["video_clock_mode"] = self._diag_clock_mode
        self._diag_record_event(
            "tracks_prebuffered",
            audio_buffer_ms=round(self._audio_buffer_seconds * 1000, 1),
            video_queue_frames=self._video_queue.qsize(),
            cross_track_pts_delta_ignored=True,
        )

    async def patched_receive_video(self) -> None:
        async for frame in self.client.getVideoStreamIterator("rgb24"):
            if self.stop_event.is_set() or frame is None:
                break
            now = time.monotonic()
            timestamp = frame_time_seconds(frame)
            if self._first_video_timestamp is None and timestamp is not None:
                self._first_video_timestamp = timestamp
                self._diag_record_event("first_video_received", source_pts=round(timestamp, 6))
            if timestamp is not None and self._diag_previous_video_pts is not None:
                delta = timestamp - self._diag_previous_video_pts
                if 0 < delta < 1:
                    self._diag_video_pts_deltas.append(delta)
            if timestamp is not None:
                self._diag_previous_video_pts = timestamp
            if self._diag_previous_video_arrival is not None:
                self._diag_video_arrival_deltas.append(now - self._diag_previous_video_arrival)
            self._diag_previous_video_arrival = now
            packet = VideoPacket(
                frame=frame,
                timestamp=timestamp,
                sequence=self._video_sequence,
            )
            self._video_sequence += 1
            if self._video_queue.full():
                try:
                    self._video_queue.get_nowait()
                    self._metrics["video_queue_drops"] += 1
                except asyncio.QueueEmpty:
                    pass
            await self._video_queue.put(packet)
            self._metrics["video_frames_received"] += 1
            self._video_ready.set()
        self.stop_event.set()

    async def patched_play_audio(self) -> None:
        while not self.stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.25)
            except TimeoutError:
                self._metrics["audio_underflows"] += 1
                continue
            self._audio_buffer_seconds = max(0.0, self._audio_buffer_seconds - chunk.duration)
            if self._audio_start_monotonic is None:
                self._audio_start_monotonic = time.monotonic()
                self._audio_started.set()
                self._diag_record_event("audio_playback_started")
            chunk_start = self._audio_samples_written / AUDIO_RATE
            levels = calculate_pcm16_levels(chunk.pcm)
            dbfs = float(levels["dbfs"])
            envelope = max(0.0, min(1.0, (dbfs + 65.0) / 45.0))
            self._diag_audio_samples.append((chunk_start, envelope))
            self._diag_audio_dbfs.append((chunk_start, dbfs))
            if self._diag_first_audio_onset is None and dbfs >= AUDIO_ACTIVE_DBFS:
                self._diag_first_audio_onset = chunk_start
                self._diag_record_event(
                    "first_non_silent_audio",
                    audio_clock_ms=round(chunk_start * 1000, 1),
                    dbfs=round(dbfs, 2),
                )
            if self._audio_stream is not None:
                await asyncio.to_thread(self._audio_stream.write, chunk.pcm)
            else:
                await asyncio.sleep(chunk.duration)
            self._audio_samples_written += chunk.samples
            self._metrics["audio_frames_played"] += 1

    def patched_video_target(self, packet: Any) -> float:
        return packet.sequence / DEFAULT_VIDEO_FPS + self.video_delay_seconds

    async def patched_display_video(self) -> None:
        await self._audio_started.wait()
        cv2 = self.cv2
        while not self.stop_event.is_set():
            try:
                packet = await asyncio.wait_for(self._video_queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            target = self._video_target(packet)
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
            image = packet.frame.to_ndarray()
            motion, threshold = self._diag_mouth_motion(image)
            cv2.imshow(self.window_title, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            cv2.waitKey(1)
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
            try:
                if cv2.getWindowProperty(self.window_title, cv2.WND_PROP_VISIBLE) < 1:
                    self.stop_event.set()
                    break
            except Exception:
                pass
            await asyncio.sleep(0)

    def diagnostic_report(self, window_start: float | None = None) -> dict[str, Any]:
        audio = list(self._diag_audio_samples)
        video = list(self._diag_video_samples)
        speed = list(self._diag_speed_samples)
        dbfs = list(self._diag_audio_dbfs)
        if window_start is not None:
            audio = [row for row in audio if row[0] >= window_start]
            video = [row for row in video if row[0] >= window_start]
            dbfs = [row for row in dbfs if row[0] >= window_start]
            wall_start = max(0.0, window_start)
            speed = [row for row in speed if row[1] >= wall_start]

        lag_result = estimate_signal_lag(audio, video)
        motion_values = [value for _, value in video]
        motion_baseline = statistics.median(motion_values[: min(30, len(motion_values))]) if motion_values else 0.0
        motion_threshold = max(1.2, motion_baseline * 2.4 + 0.35)
        audio_onset = _find_onset(dbfs, AUDIO_ACTIVE_DBFS)
        mouth_onset = _find_onset(video, motion_threshold)
        if audio_onset is None:
            audio_onset = self._diag_first_audio_onset
        if mouth_onset is None:
            mouth_onset = self._diag_first_mouth_onset
        onset_offset = None
        if audio_onset is not None and mouth_onset is not None:
            onset_offset = round((mouth_onset - audio_onset) * 1000, 1)

        report = {
            "diagnostic_version": DIAGNOSTIC_VERSION,
            "session_id": self._diag_session_id,
            "generated_at": utc_iso(),
            "clock_mode": self._diag_clock_mode,
            "expected_video_fps": DEFAULT_VIDEO_FPS,
            "source_pts_fps": median_fps(self._diag_video_pts_deltas),
            "receive_fps": median_fps(self._diag_video_arrival_deltas),
            "render_fps_recent": median_fps(self._diag_video_render_deltas),
            "video_playback_speed_ratio": timeline_speed_ratio(speed),
            "first_non_silent_audio_ms": round(audio_onset * 1000, 1) if audio_onset is not None else None,
            "first_mouth_motion_ms": round(mouth_onset * 1000, 1) if mouth_onset is not None else None,
            "first_onset_offset_ms": onset_offset,
            "estimated_lip_sync_offset_ms": lag_result["lag_ms"],
            "correlation": lag_result["correlation"],
            "correlation_confidence": lag_result["confidence"],
            "correlation_points": lag_result["points"],
            "audio_sample_count": len(audio),
            "video_sample_count": len(video),
            "audio_peak_dbfs": round(max((value for _, value in dbfs), default=-96.0), 2),
            "mouth_motion_peak": round(max(motion_values, default=0.0), 4),
            "scheduler_lateness_ms": self._metrics.get("scheduler_lateness_ms"),
            "audio_underflows": self._metrics.get("audio_underflows", 0),
            "video_frames_dropped": self._metrics.get("video_frames_dropped", 0),
            "event_log_path": str(self._diag_event_path),
            "report_path": str(self._diag_report_path),
        }
        conclusion, problems, suggestions = build_diagnostic_conclusion(report)
        report["conclusion_zh"] = conclusion
        report["problems"] = problems
        report["suggestions"] = suggestions
        self._diag_last_report = report
        return report

    async def run_diagnostic(self, duration_seconds: float = 12.0) -> dict[str, Any]:
        duration = max(5.0, min(float(duration_seconds), 30.0))
        window_start = self._audio_playhead()
        self._diag_record_event("diagnostic_started", duration_seconds=duration, window_start=window_start)
        await asyncio.sleep(duration)
        report = self._diag_diagnostic_report(window_start)
        self._diag_record_event(
            "diagnostic_completed",
            conclusion_zh=report["conclusion_zh"],
            estimated_lip_sync_offset_ms=report["estimated_lip_sync_offset_ms"],
            video_playback_speed_ratio=report["video_playback_speed_ratio"],
        )
        try:
            DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
            self._diag_report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        return report

    def patched_status(self) -> dict[str, Any]:
        values = dict(self._metrics)
        values.update(
            {
                "audio_buffer_ms": round(self._audio_buffer_seconds * 1000, 1),
                "audio_queue_size": self._audio_queue.qsize(),
                "video_queue_size": self._video_queue.qsize(),
                "audio_clock_seconds": round(self._audio_playhead(), 3),
                "video_clock_seconds": round(self._last_video_clock, 3),
                "video_clock_mode": self._diag_clock_mode,
                "source_pts_fps": median_fps(self._diag_video_pts_deltas),
                "receive_fps": median_fps(self._diag_video_arrival_deltas),
                "render_fps_recent": median_fps(self._diag_video_render_deltas),
                "video_playback_speed_ratio": timeline_speed_ratio(list(self._diag_speed_samples)),
                "event_log_path": str(self._diag_event_path),
                "report_path": str(self._diag_report_path),
            }
        )
        report = self._diag_diagnostic_report()
        values["objective_diagnostics"] = report
        lag = report.get("estimated_lip_sync_offset_ms")
        confidence = report.get("correlation_confidence")
        onset = report.get("first_onset_offset_ms")
        measured = lag if lag is not None and confidence in {"medium", "high"} else onset
        if values.get("status") != "active":
            values["sync_health"] = values.get("status", "starting")
        elif measured is None:
            values["sync_health"] = "measuring"
        elif abs(float(measured)) <= 120:
            values["sync_health"] = "good"
        elif abs(float(measured)) <= 250:
            values["sync_health"] = "warning"
        else:
            values["sync_health"] = "bad"
        elapsed = max(0.001, time.monotonic() - self._started_monotonic)
        values["render_fps"] = round(values["video_frames_rendered"] / elapsed, 2)
        now = time.monotonic()
        if now - self._diag_last_snapshot >= 2.0:
            self._diag_last_snapshot = now
            self._diag_record_event(
                "sync_snapshot",
                sync_health=values["sync_health"],
                first_onset_offset_ms=report.get("first_onset_offset_ms"),
                estimated_lip_sync_offset_ms=report.get("estimated_lip_sync_offset_ms"),
                video_playback_speed_ratio=report.get("video_playback_speed_ratio"),
                source_pts_fps=report.get("source_pts_fps"),
                render_fps_recent=report.get("render_fps_recent"),
            )
        return values

    async def patched_close(self) -> None:
        if not self.stop_event.is_set() or self._metrics.get("status") != "ended":
            report = self._diag_diagnostic_report()
            self._diag_record_event("renderer_closing", conclusion_zh=report["conclusion_zh"])
            try:
                DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
                self._diag_report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        await original_close(self)

    renderer_class.__init__ = patched_init
    renderer_class._diag_record_event = diag_record_event
    renderer_class._diag_mouth_motion = diag_mouth_motion
    renderer_class._wait_for_prebuffer = patched_wait_for_prebuffer
    renderer_class._receive_video = patched_receive_video
    renderer_class._play_audio = patched_play_audio
    renderer_class._video_target = patched_video_target
    renderer_class._display_video = patched_display_video
    renderer_class._diag_diagnostic_report = diagnostic_report
    renderer_class.run_diagnostic = run_diagnostic
    renderer_class.status = patched_status
    renderer_class.close = patched_close
    renderer_class._aliver_diagnostics_v2 = True


def find_runtime(manager: Any, session_id: str | None = None) -> Any:
    sessions = getattr(manager, "sessions", {})
    if session_id:
        runtime = sessions.get(session_id)
        if runtime is not None:
            return runtime
    active = [
        runtime
        for runtime in sessions.values()
        if getattr(runtime, "state", {}).get("status") in {"active", "starting"}
    ]
    if active:
        return active[0]
    if sessions:
        return next(iter(sessions.values()))
    raise RuntimeError("当前 Bridge 没有可诊断的 Simli 会话。")


async def run_manager_diagnostic(
    manager: Any,
    *,
    session_id: str | None = None,
    duration_seconds: float = 12.0,
) -> dict[str, Any]:
    runtime = find_runtime(manager, session_id)
    renderer = getattr(runtime, "renderer", None)
    if renderer is None or not hasattr(renderer, "run_diagnostic"):
        raise RuntimeError("Simli 同步诊断器尚未初始化。")
    return await renderer.run_diagnostic(duration_seconds)


def manager_diagnostic_report(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = find_runtime(manager, session_id)
    renderer = getattr(runtime, "renderer", None)
    if renderer is None or not hasattr(renderer, "_diag_diagnostic_report"):
        raise RuntimeError("Simli 同步诊断器尚未初始化。")
    return renderer._diag_diagnostic_report()
