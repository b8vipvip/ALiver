from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass
from typing import Any

try:
    from bridge.audio_capture import _load_pyaudio, normalize_device_name
except ModuleNotFoundError:
    from audio_capture import _load_pyaudio, normalize_device_name

AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
DEFAULT_VIDEO_FPS = 30.0
_CONFIG: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "aliver_simli_sync_config", default={}
)
_PATCHED = False


def clamp(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def frame_time_seconds(frame: Any) -> float | None:
    value = getattr(frame, "time", None)
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    pts = getattr(frame, "pts", None)
    time_base = getattr(frame, "time_base", None)
    if pts is None or time_base is None:
        return None
    try:
        return float(pts * time_base)
    except (TypeError, ValueError):
        return None


def interleaved_pcm16(array: Any) -> bytes:
    """Convert a PyAV ndarray to packed little-endian stereo PCM16."""
    shape = tuple(getattr(array, "shape", ()) or ())
    ndim = int(getattr(array, "ndim", len(shape)) or 0)
    if ndim == 2 and len(shape) == 2 and shape[0] == AUDIO_CHANNELS:
        return array.T.copy().tobytes()
    return array.tobytes()


@dataclass(slots=True)
class AudioChunk:
    pcm: bytes
    timestamp: float | None
    samples: int

    @property
    def duration(self) -> float:
        return self.samples / AUDIO_RATE if self.samples > 0 else 0.0


@dataclass(slots=True)
class VideoPacket:
    frame: Any
    timestamp: float | None
    sequence: int


class SimliSynchronizedRenderer:
    """Audio-master renderer for Simli video, audio and streaming-software output."""

    def __init__(
        self,
        client: Any,
        *,
        window_title: str,
        window_size: list[int],
        always_on_top: bool,
        play_return_audio: bool,
        audio_output_device_index: int | None,
    ) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "缺少 opencv-python，无法显示 Simli 数字人窗口。请重新安装 requirements.txt。"
            ) from exc

        config = dict(_CONFIG.get() or {})
        self.client = client
        self.cv2 = cv2
        self.window_title = window_title
        self.window_size = (int(window_size[0]), int(window_size[1]))
        self.always_on_top = always_on_top
        self.play_return_audio = play_return_audio
        self.audio_output_device_index = audio_output_device_index
        self.audio_output_device_name = str(config.get("audio_output_device_name") or "").strip()
        self.auto_live_out = bool(config.get("auto_live_out", True))
        self.prebuffer_seconds = clamp(config.get("sync_prebuffer_ms"), 80, 2000, 350) / 1000
        self.video_delay_seconds = clamp(config.get("video_delay_ms"), -1000, 2000, 0) / 1000
        self.late_drop_seconds = clamp(config.get("late_video_drop_ms"), 50, 1000, 180) / 1000

        self.stop_event = asyncio.Event()
        self._audio_queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=240)
        self._video_queue: asyncio.Queue[VideoPacket] = asyncio.Queue(maxsize=180)
        self._audio_ready = asyncio.Event()
        self._video_ready = asyncio.Event()
        self._audio_started = asyncio.Event()
        self._audio_buffer_seconds = 0.0
        self._audio = None
        self._audio_stream = None
        self._audio_resampler = None
        self._audio_start_monotonic: float | None = None
        self._audio_output_latency = 0.0
        self._audio_samples_written = 0
        self._first_audio_timestamp: float | None = None
        self._first_video_timestamp: float | None = None
        self._timeline_start_delta = 0.0
        self._last_video_clock = 0.0
        self._video_sequence = 0
        self._started_monotonic = time.monotonic()
        self._tasks: list[asyncio.Task] = []
        self._metrics: dict[str, Any] = {
            "mode": "audio_master",
            "status": "starting",
            "prebuffer_ms": round(self.prebuffer_seconds * 1000),
            "video_delay_ms": round(self.video_delay_seconds * 1000),
            "late_video_drop_ms": round(self.late_drop_seconds * 1000),
            "audio_output_device": None,
            "audio_output_device_index": None,
            "audio_output_latency_ms": 0.0,
            "audio_frames_received": 0,
            "audio_frames_played": 0,
            "video_frames_received": 0,
            "video_frames_rendered": 0,
            "video_frames_dropped": 0,
            "audio_queue_drops": 0,
            "video_queue_drops": 0,
            "audio_underflows": 0,
            "av_offset_ms": 0.0,
            "sync_health": "starting",
            "warning": None,
        }

    async def render(self) -> None:
        self._open_window()
        self._open_audio_output()
        receivers = [
            asyncio.create_task(self._receive_audio(), name="simli-audio-receive"),
            asyncio.create_task(self._receive_video(), name="simli-video-receive"),
        ]
        self._tasks.extend(receivers)
        try:
            await self._wait_for_prebuffer()
            players = [
                asyncio.create_task(self._play_audio(), name="simli-audio-play"),
                asyncio.create_task(self._display_video(), name="simli-video-sync"),
            ]
            self._tasks.extend(players)
            self._metrics["status"] = "active"
            done, _pending = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
            self.stop_event.set()
        finally:
            await self.close()

    def _open_window(self) -> None:
        cv2 = self.cv2
        cv2.namedWindow(self.window_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_title, *self.window_size)
        if self.always_on_top and hasattr(cv2, "WND_PROP_TOPMOST"):
            try:
                cv2.setWindowProperty(self.window_title, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass

    @staticmethod
    def _iter_output_devices(audio: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for info in audio.get_device_info_generator():
            if int(info.get("maxOutputChannels") or 0) > 0:
                rows.append(dict(info))
        return rows

    def _resolve_output_device(self, audio: Any) -> dict[str, Any]:
        devices = self._iter_output_devices(audio)
        if self.audio_output_device_index is not None:
            info = dict(audio.get_device_info_by_index(int(self.audio_output_device_index)))
            if int(info.get("maxOutputChannels") or 0) <= 0:
                raise RuntimeError("配置的 Simli 音频输出设备不支持播放。")
            return info

        wanted = normalize_device_name(self.audio_output_device_name)
        if wanted:
            exact = next(
                (row for row in devices if normalize_device_name(str(row.get("name", ""))) == wanted),
                None,
            )
            partial = next(
                (row for row in devices if wanted in normalize_device_name(str(row.get("name", "")))),
                None,
            )
            match = exact or partial
            if not match:
                raise RuntimeError(f"未找到配置的 LIVE_OUT 音频设备：{self.audio_output_device_name}")
            return match

        if self.auto_live_out:
            priorities = (
                "cable-b input",
                "live_out",
                "live out",
                "voicemeeter aux input",
                "voicemeeter vaio3 input",
            )
            for keyword in priorities:
                match = next(
                    (
                        row
                        for row in devices
                        if keyword in normalize_device_name(str(row.get("name", "")))
                    ),
                    None,
                )
                if match:
                    return match

        self._metrics["warning"] = (
            "未发现独立 LIVE_OUT 虚拟声卡，当前同步音频输出到 Windows 默认播放设备。"
        )
        return dict(audio.get_default_output_device_info())

    def _open_audio_output(self) -> None:
        if not self.play_return_audio:
            self._metrics["warning"] = "play_return_audio=false：只同步画面，不向直播输出声音。"
            return
        pyaudio = _load_pyaudio()
        self._audio = pyaudio.PyAudio()
        info = self._resolve_output_device(self._audio)
        selected_index = int(info["index"])
        self._audio_stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            output=True,
            output_device_index=selected_index,
            frames_per_buffer=960,
        )
        try:
            self._audio_output_latency = max(0.0, float(self._audio_stream.get_output_latency()))
        except Exception:
            self._audio_output_latency = 0.0
        self._metrics.update(
            {
                "audio_output_device": str(info.get("name", selected_index)),
                "audio_output_device_index": selected_index,
                "audio_output_latency_ms": round(self._audio_output_latency * 1000, 1),
            }
        )

    async def _receive_audio(self) -> None:
        try:
            from av.audio.resampler import AudioResampler
        except ImportError as exc:
            raise RuntimeError("缺少 PyAV，无法处理 Simli 返回音频。") from exc
        self._audio_resampler = AudioResampler(format="s16", layout="stereo", rate=AUDIO_RATE)
        async for frame in self.client.getAudioStreamIterator(AUDIO_RATE):
            if self.stop_event.is_set() or frame is None:
                break
            output_frames = self._audio_resampler.resample(frame)
            if output_frames is None:
                continue
            if not isinstance(output_frames, (list, tuple)):
                output_frames = [output_frames]
            for output_frame in output_frames:
                pcm = interleaved_pcm16(output_frame.to_ndarray())
                samples = len(pcm) // (2 * AUDIO_CHANNELS)
                if samples <= 0:
                    continue
                timestamp = frame_time_seconds(output_frame)
                if timestamp is None:
                    timestamp = frame_time_seconds(frame)
                chunk = AudioChunk(pcm=pcm, timestamp=timestamp, samples=samples)
                if self._first_audio_timestamp is None and timestamp is not None:
                    self._first_audio_timestamp = timestamp
                await self._put_audio(chunk)
                self._metrics["audio_frames_received"] += 1
                self._audio_ready.set()
        self.stop_event.set()

    async def _put_audio(self, chunk: AudioChunk) -> None:
        if self._audio_queue.full():
            try:
                old = self._audio_queue.get_nowait()
                self._audio_buffer_seconds = max(0.0, self._audio_buffer_seconds - old.duration)
                self._metrics["audio_queue_drops"] += 1
            except asyncio.QueueEmpty:
                pass
        await self._audio_queue.put(chunk)
        self._audio_buffer_seconds += chunk.duration

    async def _receive_video(self) -> None:
        async for frame in self.client.getVideoStreamIterator("rgb24"):
            if self.stop_event.is_set() or frame is None:
                break
            timestamp = frame_time_seconds(frame)
            if self._first_video_timestamp is None and timestamp is not None:
                self._first_video_timestamp = timestamp
            packet = VideoPacket(frame=frame, timestamp=timestamp, sequence=self._video_sequence)
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

    async def _wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 8.0
        while not self.stop_event.is_set():
            has_tracks = self._audio_ready.is_set() and self._video_ready.is_set()
            if has_tracks and self._audio_buffer_seconds >= self.prebuffer_seconds:
                break
            if time.monotonic() >= deadline:
                if not has_tracks:
                    raise RuntimeError("等待 Simli 音视频轨超时，未同时收到音频和视频。")
                break
            await asyncio.sleep(0.02)
        if self._first_audio_timestamp is not None and self._first_video_timestamp is not None:
            delta = self._first_video_timestamp - self._first_audio_timestamp
            self._timeline_start_delta = delta if abs(delta) <= 5.0 else 0.0
        self._metrics["timeline_start_delta_ms"] = round(self._timeline_start_delta * 1000, 1)

    async def _play_audio(self) -> None:
        self._audio_start_monotonic = time.monotonic()
        self._audio_started.set()
        while not self.stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.25)
            except TimeoutError:
                self._metrics["audio_underflows"] += 1
                continue
            self._audio_buffer_seconds = max(0.0, self._audio_buffer_seconds - chunk.duration)
            if self._audio_stream is not None:
                await asyncio.to_thread(self._audio_stream.write, chunk.pcm)
            else:
                await asyncio.sleep(chunk.duration)
            self._audio_samples_written += chunk.samples
            self._metrics["audio_frames_played"] += 1

    def _audio_playhead(self) -> float:
        if self._audio_start_monotonic is None:
            return 0.0
        elapsed = time.monotonic() - self._audio_start_monotonic - self._audio_output_latency
        sample_clock = self._audio_samples_written / AUDIO_RATE
        if self._audio_stream is None:
            return max(0.0, min(elapsed, sample_clock + self.prebuffer_seconds))
        return max(0.0, min(elapsed, sample_clock))

    def _video_target(self, packet: VideoPacket) -> float:
        if packet.timestamp is not None and self._first_video_timestamp is not None:
            relative = packet.timestamp - self._first_video_timestamp
        else:
            relative = packet.sequence / DEFAULT_VIDEO_FPS
        return relative + self._timeline_start_delta + self.video_delay_seconds

    async def _display_video(self) -> None:
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
            cv2.imshow(self.window_title, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            cv2.waitKey(1)
            self._last_video_clock = target
            self._metrics["video_frames_rendered"] += 1
            self._metrics["av_offset_ms"] = round((target - playhead) * 1000, 1)
            try:
                if cv2.getWindowProperty(self.window_title, cv2.WND_PROP_VISIBLE) < 1:
                    self.stop_event.set()
                    break
            except Exception:
                pass
            await asyncio.sleep(0)

    def status(self) -> dict[str, Any]:
        values = dict(self._metrics)
        values.update(
            {
                "audio_buffer_ms": round(self._audio_buffer_seconds * 1000, 1),
                "audio_queue_size": self._audio_queue.qsize(),
                "video_queue_size": self._video_queue.qsize(),
                "audio_clock_seconds": round(self._audio_playhead(), 3),
                "video_clock_seconds": round(self._last_video_clock, 3),
            }
        )
        offset = abs(float(values.get("av_offset_ms") or 0.0))
        if values.get("status") != "active":
            values["sync_health"] = values.get("status", "starting")
        elif offset <= 80:
            values["sync_health"] = "good"
        elif offset <= 200:
            values["sync_health"] = "warning"
        else:
            values["sync_health"] = "bad"
        elapsed = max(0.001, time.monotonic() - self._started_monotonic)
        values["render_fps"] = round(values["video_frames_rendered"] / elapsed, 2)
        return values

    async def close(self) -> None:
        if self.stop_event.is_set() and self._metrics.get("status") == "ended":
            return
        self.stop_event.set()
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current and not task.done():
                task.cancel()
        pending = [task for task in self._tasks if task is not current]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._audio_stream is not None:
            try:
                await asyncio.to_thread(self._audio_stream.stop_stream)
            except Exception:
                pass
            try:
                await asyncio.to_thread(self._audio_stream.close)
            except Exception:
                pass
            self._audio_stream = None
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None
        try:
            self.cv2.destroyWindow(self.window_title)
        except Exception:
            pass
        self._metrics["status"] = "ended"


def install_simli_sync_patch(simli_session_module: Any) -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    simli_session_module.SimliLocalRenderer = SimliSynchronizedRenderer
    runtime_class = simli_session_module.SimliRuntime
    original_start = runtime_class.start
    original_status = runtime_class.status

    async def patched_start(runtime: Any) -> dict[str, Any]:
        token = _CONFIG.set(dict(runtime.config))
        try:
            return await original_start(runtime)
        finally:
            _CONFIG.reset(token)

    def patched_status(runtime: Any) -> dict[str, Any]:
        values = original_status(runtime)
        renderer = getattr(runtime, "renderer", None)
        if renderer is not None and hasattr(renderer, "status"):
            values["av_sync"] = renderer.status()
        return values

    runtime_class.start = patched_start
    runtime_class.status = patched_status
