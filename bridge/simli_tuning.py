from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.audio_capture import calculate_pcm16_levels
from bridge.runtime_diagnostics import event
from bridge.simli_diagnostics import estimate_signal_lag, median_fps, timeline_speed_ratio
from bridge.simli_sync import AUDIO_RATE, _CONFIG, frame_time_seconds

BASE_DIR = Path(__file__).resolve().parent
PROFILE_PATH = BASE_DIR / "diagnostics" / "simli-tuning-profile.json"
PROFILE_VERSION = 1
CLOCK_MODES = {"source_pts", "arrival_clock", "fixed_fps"}
DEFAULT_TUNING: dict[str, Any] = {
    "clock_mode": "source_pts",
    "target_fps": 30.0,
    "playback_speed": 1.0,
    "video_delay_ms": 0,
    "sync_prebuffer_ms": 350,
    "late_video_drop_ms": 250,
    "audio_active_dbfs": -50.0,
    "mouth_sensitivity": 1.0,
}


@dataclass(slots=True)
class TunedVideoPacket:
    frame: Any
    timestamp: float | None
    sequence: int
    arrival_monotonic: float


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def normalize_tuning(values: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {**DEFAULT_TUNING, **(values or {})}
    clock_mode = str(source.get("clock_mode") or DEFAULT_TUNING["clock_mode"])
    if clock_mode not in CLOCK_MODES:
        clock_mode = str(DEFAULT_TUNING["clock_mode"])
    return {
        "clock_mode": clock_mode,
        "target_fps": round(_clamp_float(source.get("target_fps"), 10.0, 60.0, 30.0), 3),
        "playback_speed": round(
            _clamp_float(source.get("playback_speed"), 0.5, 2.0, 1.0), 4
        ),
        "video_delay_ms": _clamp_int(source.get("video_delay_ms"), -5000, 5000, 0),
        "sync_prebuffer_ms": _clamp_int(
            source.get("sync_prebuffer_ms"), 80, 3000, 350
        ),
        "late_video_drop_ms": _clamp_int(
            source.get("late_video_drop_ms"), 50, 2000, 250
        ),
        "audio_active_dbfs": round(
            _clamp_float(source.get("audio_active_dbfs"), -75.0, -20.0, -50.0), 2
        ),
        "mouth_sensitivity": round(
            _clamp_float(source.get("mouth_sensitivity"), 0.5, 4.0, 1.0), 3
        ),
    }


def load_tuning_profile() -> dict[str, Any]:
    try:
        raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return normalize_tuning()
    if isinstance(raw, dict) and isinstance(raw.get("settings"), dict):
        return normalize_tuning(raw["settings"])
    return normalize_tuning(raw if isinstance(raw, dict) else {})


def save_tuning_profile(values: dict[str, Any]) -> dict[str, Any]:
    settings = normalize_tuning(values)
    payload = {
        "profile_version": PROFILE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
    }
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def _find_sustained_onset(
    samples: list[tuple[float, float]],
    threshold: float,
    *,
    minimum_hits: int = 3,
    window_size: int = 5,
    start_at: float = 0.0,
    end_at: float | None = None,
) -> float | None:
    eligible = [
        row
        for row in samples
        if row[0] >= start_at and (end_at is None or row[0] <= end_at)
    ]
    for index in range(len(eligible)):
        window = eligible[index : index + window_size]
        if len(window) < minimum_hits:
            break
        if sum(1 for _at, value in window if value >= threshold) >= minimum_hits:
            return window[0][0]
    return None


def _robust_motion_threshold(
    video_samples: list[tuple[float, float]],
    *,
    before: float | None,
    sensitivity: float,
) -> float:
    baseline_values = [
        value
        for at, value in video_samples
        if before is None or at < max(0.0, before - 0.15)
    ]
    if len(baseline_values) > 160:
        baseline_values = baseline_values[-160:]
    if not baseline_values:
        return round(1.2 * sensitivity, 4)
    center = statistics.median(baseline_values)
    deviations = [abs(value - center) for value in baseline_values]
    mad = statistics.median(deviations) if deviations else 0.0
    threshold = max(1.2, center + max(0.55, 5.0 * mad)) * sensitivity
    return round(threshold, 4)


def build_tuning_recommendation(
    report: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    settings = normalize_tuning(current)
    recommended = dict(settings)
    reasons: list[str] = []

    lag = report.get("wall_lip_sync_offset_ms")
    confidence = str(report.get("wall_correlation_confidence") or "insufficient")
    onset = report.get("wall_first_onset_offset_ms")
    measured: float | None = None
    measurement_source = "none"
    if lag is not None and confidence in {"medium", "high"}:
        measured = float(lag)
        measurement_source = "correlation"
    elif onset is not None and abs(float(onset)) <= 8000:
        measured = float(onset)
        measurement_source = "onset_low_confidence"

    if measured is not None:
        corrected = int(round(settings["video_delay_ms"] - measured))
        recommended["video_delay_ms"] = max(-5000, min(5000, corrected))
        if measured < -120:
            reasons.append(
                f"口型比声音早约 {abs(measured):.0f} ms，建议增加视频延迟。"
            )
        elif measured > 120:
            reasons.append(f"口型比声音晚约 {measured:.0f} ms，建议减少视频延迟。")
        else:
            reasons.append("音画起点偏差已在约 120 ms 内。")

    speed_ratio = report.get("wall_video_speed_ratio")
    if speed_ratio is not None:
        ratio = float(speed_ratio)
        if 0.2 <= ratio < 0.88 or 1.12 < ratio <= 4.0:
            adjusted = settings["playback_speed"] / ratio
            recommended["playback_speed"] = round(max(0.5, min(2.0, adjusted)), 3)
            reasons.append(
                f"检测到视频时间轴约为实时的 {ratio:.2f} 倍，已给出速度修正。"
            )

    source_fps = report.get("source_pts_fps")
    if source_fps is not None and 8 <= float(source_fps) <= 60:
        recommended["clock_mode"] = "source_pts"
        reasons.append("Simli 返回了可用 PTS，建议由源时间戳控制速度而不是按帧号硬算。")
    elif report.get("receive_fps") is not None:
        recommended["clock_mode"] = "arrival_clock"
        reasons.append("源 PTS 不稳定，建议临时使用到达时钟。")

    scheduler_lateness = abs(float(report.get("scheduler_lateness_ms") or 0.0))
    if scheduler_lateness > 500:
        recommended["late_video_drop_ms"] = min(900, max(250, int(scheduler_lateness * 0.35)))
        reasons.append("调度积压明显，建议扩大少量容忍并丢弃过时帧，避免慢放追赶。")

    recommended = normalize_tuning(recommended)
    auto_apply_allowed = measurement_source == "correlation" and confidence in {"medium", "high"}
    return {
        "settings": recommended,
        "measurement_source": measurement_source,
        "confidence": confidence,
        "auto_apply_allowed": auto_apply_allowed,
        "reasons": reasons or ["数据量不足，暂时保留当前参数。"],
    }


def install_simli_tuning_patch(renderer_class: type) -> None:
    if getattr(renderer_class, "_aliver_tuning_v1", False):
        return

    original_init = renderer_class.__init__
    original_status = renderer_class.status
    original_close = renderer_class.close

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        config = dict(_CONFIG.get() or {})
        profile = load_tuning_profile()
        incoming = {
            "clock_mode": config.get("video_clock_mode", config.get("clock_mode")),
            "target_fps": config.get("target_video_fps", config.get("target_fps")),
            "playback_speed": config.get("video_playback_speed", config.get("playback_speed")),
            "video_delay_ms": config.get("video_delay_ms"),
            "sync_prebuffer_ms": config.get("sync_prebuffer_ms"),
            "late_video_drop_ms": config.get("late_video_drop_ms"),
            "audio_active_dbfs": config.get("audio_active_dbfs"),
            "mouth_sensitivity": config.get("mouth_sensitivity"),
        }
        supplied = {key: value for key, value in incoming.items() if value is not None}
        base_settings = normalize_tuning(supplied)
        # A saved machine profile is the final authority because it is edited from the tuning page.
        self._tuning = normalize_tuning(
            {**base_settings, **profile} if PROFILE_PATH.exists() else base_settings
        )
        self.prebuffer_seconds = self._tuning["sync_prebuffer_ms"] / 1000
        self.video_delay_seconds = self._tuning["video_delay_ms"] / 1000
        self.late_drop_seconds = self._tuning["late_video_drop_ms"] / 1000
        self._tuning_epoch_audio = 0.0
        self._tuning_epoch_sequence: int | None = None
        self._tuning_epoch_pts: float | None = None
        self._tuning_epoch_arrival: float | None = None
        self._tuning_last_presented_target: float | None = None
        self._tuning_generation = 1
        self._tuning_test_started: float | None = None
        self._tuning_test_deadline: float | None = None
        self._tuning_test_id: str | None = None
        self._tuning_wall_audio: deque[tuple[float, float]] = deque(maxlen=10000)
        self._tuning_wall_dbfs: deque[tuple[float, float]] = deque(maxlen=10000)
        self._tuning_wall_video: deque[tuple[float, float]] = deque(maxlen=10000)
        self._tuning_wall_media: deque[tuple[float, float]] = deque(maxlen=10000)
        self._tuning_latest_report: dict[str, Any] | None = None
        self._metrics.update(
            {
                "video_clock_mode": self._tuning["clock_mode"],
                "target_video_fps": self._tuning["target_fps"],
                "video_playback_speed": self._tuning["playback_speed"],
                "tuning_profile_path": str(PROFILE_PATH),
            }
        )
        event("simli_tuning_initialized", settings=self._tuning)

    def reanchor(self) -> None:
        self._tuning_epoch_audio = self._audio_playhead()
        self._tuning_epoch_sequence = None
        self._tuning_epoch_pts = None
        self._tuning_epoch_arrival = None
        self._tuning_last_presented_target = None
        self._tuning_generation += 1

    def tuning_snapshot(self) -> dict[str, Any]:
        return {
            "settings": dict(self._tuning),
            "profile_path": str(PROFILE_PATH.resolve()),
            "profile_exists": PROFILE_PATH.exists(),
            "generation": self._tuning_generation,
            "live_apply_fields": [
                "clock_mode",
                "target_fps",
                "playback_speed",
                "video_delay_ms",
                "late_video_drop_ms",
                "audio_active_dbfs",
                "mouth_sensitivity",
            ],
            "next_session_fields": ["sync_prebuffer_ms"],
            "latest_test": self._tuning_latest_report,
        }

    def apply_tuning(self, values: dict[str, Any], *, persist: bool = False) -> dict[str, Any]:
        merged = normalize_tuning({**self._tuning, **(values or {})})
        changed = {
            key: {"before": self._tuning.get(key), "after": value}
            for key, value in merged.items()
            if self._tuning.get(key) != value
        }
        self._tuning = merged
        self.prebuffer_seconds = merged["sync_prebuffer_ms"] / 1000
        self.video_delay_seconds = merged["video_delay_ms"] / 1000
        self.late_drop_seconds = merged["late_video_drop_ms"] / 1000
        self._metrics.update(
            {
                "prebuffer_ms": merged["sync_prebuffer_ms"],
                "video_delay_ms": merged["video_delay_ms"],
                "late_video_drop_ms": merged["late_video_drop_ms"],
                "video_clock_mode": merged["clock_mode"],
                "target_video_fps": merged["target_fps"],
                "video_playback_speed": merged["playback_speed"],
            }
        )
        self._tuning_reanchor()
        if persist:
            save_tuning_profile(merged)
        event("simli_tuning_applied", changed=changed, persist=persist, settings=merged)
        result = self._tuning_snapshot()
        result["changed"] = changed
        result["persisted"] = persist
        result["restart_recommended"] = "sync_prebuffer_ms" in changed
        return result

    async def patched_wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 10.0
        minimum_frames = max(
            3,
            int(self._tuning["target_fps"] * min(self.prebuffer_seconds, 0.35)),
        )
        while not self.stop_event.is_set():
            tracks_ready = self._audio_ready.is_set() and self._video_ready.is_set()
            enough_video = self._video_queue.qsize() >= minimum_frames
            enough_audio = self._audio_buffer_seconds >= self.prebuffer_seconds
            if tracks_ready and enough_video and enough_audio:
                break
            if time.monotonic() >= deadline:
                if not tracks_ready:
                    raise RuntimeError("等待 Simli 音视频轨超时，未同时收到音频和视频。")
                break
            await asyncio.sleep(0.02)
        if self._first_audio_timestamp is not None and self._first_video_timestamp is not None:
            delta = self._first_video_timestamp - self._first_audio_timestamp
            self._timeline_start_delta = delta if abs(delta) <= 8.0 else 0.0
        else:
            self._timeline_start_delta = 0.0
        self._metrics["timeline_start_delta_ms"] = round(self._timeline_start_delta * 1000, 1)
        self._metrics["video_clock_mode"] = self._tuning["clock_mode"]
        self._diag_record_event(
            "tracks_prebuffered_tuned",
            audio_buffer_ms=round(self._audio_buffer_seconds * 1000, 1),
            video_queue_frames=self._video_queue.qsize(),
            timeline_start_delta_ms=self._metrics["timeline_start_delta_ms"],
            tuning=self._tuning,
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
            packet = TunedVideoPacket(
                frame=frame,
                timestamp=timestamp,
                sequence=self._video_sequence,
                arrival_monotonic=now,
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
            envelope = max(0.0, min(1.0, (dbfs + 70.0) / 50.0))
            self._diag_audio_samples.append((chunk_start, envelope))
            self._diag_audio_dbfs.append((chunk_start, dbfs))
            if self._diag_first_audio_onset is None and dbfs >= self._tuning["audio_active_dbfs"]:
                self._diag_first_audio_onset = chunk_start
                self._diag_record_event(
                    "first_non_silent_audio",
                    audio_clock_ms=round(chunk_start * 1000, 1),
                    dbfs=round(dbfs, 2),
                )
            write_started = time.monotonic()
            if self._audio_stream is not None:
                await asyncio.to_thread(self._audio_stream.write, chunk.pcm)
            else:
                await asyncio.sleep(chunk.duration)
            if self._tuning_test_started is not None:
                audible_at = write_started + self._audio_output_latency
                wall = audible_at - self._tuning_test_started
                if wall >= 0:
                    self._tuning_wall_audio.append((wall, envelope))
                    self._tuning_wall_dbfs.append((wall, dbfs))
            self._audio_samples_written += chunk.samples
            self._metrics["audio_frames_played"] += 1

    def tuned_video_target(self, packet: TunedVideoPacket) -> float:
        mode = self._tuning["clock_mode"]
        if self._tuning_epoch_sequence is None:
            self._tuning_epoch_sequence = packet.sequence
            self._tuning_epoch_pts = packet.timestamp
            self._tuning_epoch_arrival = packet.arrival_monotonic
        relative: float
        if (
            mode == "source_pts"
            and packet.timestamp is not None
            and self._tuning_epoch_pts is not None
            and packet.timestamp >= self._tuning_epoch_pts
        ):
            relative = packet.timestamp - self._tuning_epoch_pts
            if self._tuning_epoch_audio <= 0.001:
                relative += self._timeline_start_delta
        elif mode == "arrival_clock" and self._tuning_epoch_arrival is not None:
            relative = packet.arrival_monotonic - self._tuning_epoch_arrival
        else:
            relative = (packet.sequence - self._tuning_epoch_sequence) / self._tuning["target_fps"]
        relative = max(0.0, relative) / self._tuning["playback_speed"]
        return self._tuning_epoch_audio + relative + self.video_delay_seconds

    async def patched_display_video(self) -> None:
        await self._audio_started.wait()
        cv2 = self.cv2
        event("simli_tuned_video_loop_started", settings=self._tuning)
        while not self.stop_event.is_set():
            try:
                packet = await asyncio.wait_for(self._video_queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            target = self._tuning_video_target(packet)
            min_interval = 1.0 / self._tuning["target_fps"]
            if (
                self._tuning_last_presented_target is not None
                and target - self._tuning_last_presented_target < min_interval * 0.72
                and not self._video_queue.empty()
            ):
                self._metrics["video_frames_dropped"] += 1
                self._metrics["fps_cap_drops"] = int(self._metrics.get("fps_cap_drops") or 0) + 1
                continue
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
            if self._tuning_test_started is not None:
                test_wall = now - self._tuning_test_started
                if test_wall >= 0:
                    self._tuning_wall_video.append((test_wall, motion))
                    self._tuning_wall_media.append((test_wall, target))
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
            self._tuning_last_presented_target = target
            self._metrics["video_frames_rendered"] += 1
            self._metrics["scheduler_lateness_ms"] = round(lateness * 1000, 1)
            self._metrics["av_offset_ms"] = round((target - playhead) * 1000, 1)
            self._guard_frame_count += 1
            try:
                if cv2.getWindowProperty(self.window_title, cv2.WND_PROP_VISIBLE) < 1:
                    self.stop_event.set()
                    break
            except Exception:
                pass
            await asyncio.sleep(0)

    def analyze_test(self) -> dict[str, Any]:
        audio = list(self._tuning_wall_audio)
        dbfs = list(self._tuning_wall_dbfs)
        video = list(self._tuning_wall_video)
        media = list(self._tuning_wall_media)
        audio_threshold = self._tuning["audio_active_dbfs"]
        audio_onset = _find_sustained_onset(
            dbfs,
            audio_threshold,
            minimum_hits=2,
            window_size=4,
        )
        motion_threshold = _robust_motion_threshold(
            video,
            before=audio_onset,
            sensitivity=self._tuning["mouth_sensitivity"],
        )
        mouth_onset = None
        if audio_onset is not None:
            mouth_onset = _find_sustained_onset(
                video,
                motion_threshold,
                minimum_hits=3,
                window_size=6,
                start_at=max(0.0, audio_onset - 8.0),
                end_at=audio_onset + 8.0,
            )
        lag_result = estimate_signal_lag(
            audio,
            video,
            max_lag_seconds=8.0,
            step_seconds=0.05,
        )
        onset_offset = None
        if audio_onset is not None and mouth_onset is not None:
            onset_offset = round((mouth_onset - audio_onset) * 1000, 1)
        speed_samples = media
        if audio_onset is not None:
            speed_samples = [row for row in media if row[0] >= max(0.0, audio_onset - 1.0)]
        speed_ratio = timeline_speed_ratio(speed_samples)
        wall_lag = lag_result["lag_ms"]
        confidence = lag_result["confidence"]
        if wall_lag is not None and confidence in {"medium", "high"}:
            summary_offset = float(wall_lag)
            summary_source = "持续相关性"
        else:
            summary_offset = float(onset_offset) if onset_offset is not None else None
            summary_source = "首次起点（低置信度）"
        if summary_offset is None:
            conclusion = "未测得足够的连续语音和口部运动，请让数字人完整朗读测试文本。"
        elif abs(summary_offset) <= 120:
            conclusion = f"{summary_source}显示音画偏差约 {summary_offset:.0f} ms，已接近同步。"
        elif summary_offset < 0:
            conclusion = f"{summary_source}显示口型比声音早约 {abs(summary_offset):.0f} ms。"
        else:
            conclusion = f"{summary_source}显示口型比声音晚约 {summary_offset:.0f} ms。"
        report = {
            "test_id": self._tuning_test_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(
                max([at for at, _value in audio + video], default=0.0), 3
            ),
            "clock_mode": self._tuning["clock_mode"],
            "configured_tuning": dict(self._tuning),
            "wall_audio_onset_ms": round(audio_onset * 1000, 1) if audio_onset is not None else None,
            "wall_mouth_onset_ms": round(mouth_onset * 1000, 1) if mouth_onset is not None else None,
            "wall_first_onset_offset_ms": onset_offset,
            "wall_lip_sync_offset_ms": wall_lag,
            "wall_correlation": lag_result["correlation"],
            "wall_correlation_confidence": confidence,
            "wall_correlation_points": lag_result["points"],
            "wall_video_speed_ratio": speed_ratio,
            "source_pts_fps": median_fps(self._diag_video_pts_deltas),
            "receive_fps": median_fps(self._diag_video_arrival_deltas),
            "render_fps_recent": median_fps(self._diag_video_render_deltas),
            "scheduler_lateness_ms": self._metrics.get("scheduler_lateness_ms"),
            "audio_output_latency_ms": self._metrics.get("audio_output_latency_ms"),
            "audio_underflows": self._metrics.get("audio_underflows", 0),
            "video_frames_dropped": self._metrics.get("video_frames_dropped", 0),
            "audio_samples": len(audio),
            "video_samples": len(video),
            "audio_threshold_dbfs": audio_threshold,
            "mouth_motion_threshold": motion_threshold,
            "conclusion_zh": conclusion,
        }
        report["recommendation"] = build_tuning_recommendation(report, self._tuning)
        self._tuning_latest_report = report
        return report

    async def run_tuning_test(self, duration_seconds: float = 16.0) -> dict[str, Any]:
        duration = _clamp_float(duration_seconds, 8.0, 30.0, 16.0)
        if self._tuning_test_started is not None:
            raise RuntimeError("已有一个同步测试正在进行，请等待当前测试结束。")
        self._tuning_wall_audio.clear()
        self._tuning_wall_dbfs.clear()
        self._tuning_wall_video.clear()
        self._tuning_wall_media.clear()
        self._tuning_test_id = datetime.now().strftime("tuning-%Y%m%d-%H%M%S")
        self._tuning_test_started = time.monotonic()
        self._tuning_test_deadline = self._tuning_test_started + duration
        event(
            "simli_tuning_test_started",
            test_id=self._tuning_test_id,
            duration_seconds=duration,
            settings=self._tuning,
        )
        try:
            await asyncio.sleep(duration)
            report = self._tuning_analyze_test()
            event("simli_tuning_test_completed", report=report)
            return report
        finally:
            self._tuning_test_started = None
            self._tuning_test_deadline = None

    def patched_status(self) -> dict[str, Any]:
        values = original_status(self)
        values["tuning"] = self._tuning_snapshot()
        if self._tuning_latest_report is not None:
            recommendation = self._tuning_latest_report.get("recommendation") or {}
            confidence = self._tuning_latest_report.get("wall_correlation_confidence")
            offset = self._tuning_latest_report.get("wall_lip_sync_offset_ms")
            if offset is None:
                offset = self._tuning_latest_report.get("wall_first_onset_offset_ms")
            values["tuning_test_health"] = (
                "good"
                if offset is not None and abs(float(offset)) <= 120
                else "warning"
                if offset is not None and abs(float(offset)) <= 300
                else "bad"
                if offset is not None
                else "measuring"
            )
            values["tuning_auto_apply_allowed"] = bool(
                recommendation.get("auto_apply_allowed")
            )
            values["tuning_test_confidence"] = confidence
        return values

    async def patched_close(self) -> None:
        self._tuning_test_started = None
        self._tuning_test_deadline = None
        await original_close(self)

    renderer_class.__init__ = patched_init
    renderer_class._tuning_reanchor = reanchor
    renderer_class._tuning_snapshot = tuning_snapshot
    renderer_class.apply_tuning = apply_tuning
    renderer_class._wait_for_prebuffer = patched_wait_for_prebuffer
    renderer_class._receive_video = patched_receive_video
    renderer_class._play_audio = patched_play_audio
    renderer_class._tuning_video_target = tuned_video_target
    renderer_class._display_video = patched_display_video
    renderer_class._tuning_analyze_test = analyze_test
    renderer_class.run_tuning_test = run_tuning_test
    renderer_class.status = patched_status
    renderer_class.close = patched_close
    renderer_class._aliver_tuning_v1 = True


def _find_runtime(manager: Any, session_id: str | None = None) -> Any | None:
    sessions = getattr(manager, "sessions", {})
    if session_id and session_id in sessions:
        return sessions[session_id]
    for runtime in sessions.values():
        if runtime.state.get("status") in {"active", "starting"}:
            return runtime
    return None


def manager_tuning_status(manager: Any, *, session_id: str | None = None) -> dict[str, Any]:
    runtime = _find_runtime(manager, session_id)
    profile = load_tuning_profile()
    if runtime is None:
        return {
            "session_active": False,
            "session_id": None,
            "settings": profile,
            "profile_path": str(PROFILE_PATH.resolve()),
            "profile_exists": PROFILE_PATH.exists(),
            "latest_test": None,
        }
    renderer = getattr(runtime, "renderer", None)
    if renderer is None or not hasattr(renderer, "_tuning_snapshot"):
        raise RuntimeError("Simli 调参器尚未初始化。")
    result = renderer._tuning_snapshot()
    result.update(
        {
            "session_active": runtime.state.get("status") == "active",
            "session_id": runtime.session_id,
            "session_status": runtime.state.get("status"),
            "av_sync": renderer.status(),
        }
    )
    return result


def manager_apply_tuning(
    manager: Any,
    *,
    settings: dict[str, Any],
    session_id: str | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    normalized = normalize_tuning(settings)
    runtime = _find_runtime(manager, session_id)
    if persist:
        save_tuning_profile(normalized)
    if runtime is None:
        return {
            "session_active": False,
            "session_id": None,
            "settings": normalized,
            "persisted": persist,
            "profile_path": str(PROFILE_PATH.resolve()),
            "restart_recommended": True,
        }
    renderer = getattr(runtime, "renderer", None)
    if renderer is None or not hasattr(renderer, "apply_tuning"):
        raise RuntimeError("Simli 调参器尚未初始化。")
    result = renderer.apply_tuning(normalized, persist=persist)
    result.update({"session_active": True, "session_id": runtime.session_id})
    return result


def manager_reset_tuning(
    manager: Any,
    *,
    session_id: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    return manager_apply_tuning(
        manager,
        settings=DEFAULT_TUNING,
        session_id=session_id,
        persist=persist,
    )


async def manager_run_tuning_test(
    manager: Any,
    *,
    session_id: str | None = None,
    duration_seconds: float = 16.0,
) -> dict[str, Any]:
    runtime = _find_runtime(manager, session_id)
    if runtime is None:
        raise RuntimeError("当前 Bridge 没有运行中的 Simli 会话。")
    renderer = getattr(runtime, "renderer", None)
    if renderer is None or not hasattr(renderer, "run_tuning_test"):
        raise RuntimeError("Simli 调参器尚未初始化。")
    return await renderer.run_tuning_test(duration_seconds)
