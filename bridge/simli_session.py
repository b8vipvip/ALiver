from __future__ import annotations

import asyncio
import audioop
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from bridge.audio_capture import AudioCaptureManager, _load_pyaudio, calculate_pcm16_levels
except ModuleNotFoundError:
    from audio_capture import AudioCaptureManager, _load_pyaudio, calculate_pcm16_levels

SIMLI_SAMPLE_RATE = 16000
SIMLI_CHANNELS = 1
SIMLI_CHUNK_BYTES = 6000


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Pcm16ToSimliConverter:
    """Streaming PCM16 converter to Simli's 16 kHz mono input format."""

    def __init__(self, source_rate: int, source_channels: int) -> None:
        self.source_rate = int(source_rate)
        self.source_channels = max(1, min(int(source_channels), 2))
        self._rate_state: tuple[Any, ...] | None = None

    def convert(self, data: bytes) -> bytes:
        if not data:
            return b""
        mono = audioop.tomono(data, 2, 0.5, 0.5) if self.source_channels == 2 else data
        if self.source_rate == SIMLI_SAMPLE_RATE:
            return mono
        converted, self._rate_state = audioop.ratecv(
            mono,
            2,
            SIMLI_CHANNELS,
            self.source_rate,
            SIMLI_SAMPLE_RATE,
            self._rate_state,
        )
        return converted


class SimliLocalRenderer:
    """Render Simli video in a capture-friendly OpenCV window and play its audio."""

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
                "opencv-python is not installed. Run the ALiver requirements installer again."
            ) from exc

        self.client = client
        self.cv2 = cv2
        self.window_title = window_title
        self.window_size = (int(window_size[0]), int(window_size[1]))
        self.always_on_top = always_on_top
        self.play_return_audio = play_return_audio
        self.audio_output_device_index = audio_output_device_index
        self.stop_event = asyncio.Event()
        self._audio = None
        self._audio_stream = None

    async def render(self) -> None:
        video_task = asyncio.create_task(self._display_video(), name="simli-video-render")
        audio_task = asyncio.create_task(self._play_audio(), name="simli-audio-render")
        try:
            await asyncio.gather(video_task, audio_task)
        finally:
            video_task.cancel()
            audio_task.cancel()
            await asyncio.gather(video_task, audio_task, return_exceptions=True)
            await self.close()

    async def _display_video(self) -> None:
        cv2 = self.cv2
        cv2.namedWindow(self.window_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_title, *self.window_size)
        if self.always_on_top and hasattr(cv2, "WND_PROP_TOPMOST"):
            try:
                cv2.setWindowProperty(self.window_title, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass
        async for frame in self.client.getVideoStreamIterator("rgb24"):
            if self.stop_event.is_set() or frame is None:
                break
            image = frame.to_ndarray()
            cv2.imshow(self.window_title, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            cv2.waitKey(1)
            try:
                if cv2.getWindowProperty(self.window_title, cv2.WND_PROP_VISIBLE) < 1:
                    self.stop_event.set()
                    break
            except Exception:
                pass
            await asyncio.sleep(0)

    async def _play_audio(self) -> None:
        if not self.play_return_audio:
            async for _frame in self.client.getAudioStreamIterator():
                if self.stop_event.is_set():
                    break
            return

        pyaudio = _load_pyaudio()
        self._audio = pyaudio.PyAudio()
        kwargs: dict[str, Any] = {
            "format": pyaudio.paInt16,
            "channels": 2,
            "rate": 48000,
            "output": True,
            "frames_per_buffer": 1024,
        }
        if self.audio_output_device_index is not None:
            kwargs["output_device_index"] = int(self.audio_output_device_index)
        self._audio_stream = self._audio.open(**kwargs)
        async for frame in self.client.getAudioStreamIterator():
            if self.stop_event.is_set() or frame is None:
                break
            self._audio_stream.write(frame.to_ndarray().tobytes())
            await asyncio.sleep(0)

    async def close(self) -> None:
        self.stop_event.set()
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop_stream()
            except Exception:
                pass
            try:
                self._audio_stream.close()
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


@dataclass
class SimliRuntime:
    session_id: str
    config: dict[str, Any]
    audio_manager: AudioCaptureManager
    client: Any | None = None
    renderer: SimliLocalRenderer | None = None
    sender_task: asyncio.Task | None = None
    renderer_task: asyncio.Task | None = None
    capture_thread: threading.Thread | None = None
    stop_flag: threading.Event = field(default_factory=threading.Event)
    audio_queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=96))
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.state = {
            "session_id": self.session_id,
            "status": "starting",
            "window_title": self.config.get("window_title", "ALiver Simli Avatar"),
            "face_id": self.config.get("face_id"),
            "transport": self.config.get("transport"),
            "source_sample_rate": None,
            "source_channels": None,
            "sent_chunks": 0,
            "sent_bytes": 0,
            "dropped_chunks": 0,
            "last_input_dbfs": -96.0,
            "started_at": None,
            "stopped_at": None,
            "error": None,
        }

    async def start(self) -> dict[str, Any]:
        if self.audio_manager.status().get("active"):
            raise RuntimeError("Stop the GPT_OUT capture test before starting Simli.")
        try:
            from simli import SimliClient, SimliConfig
            from simli.simli import SimliModels, TransportMode
        except ImportError as exc:
            raise RuntimeError(
                "simli-ai is not installed. Run .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ) from exc

        model = SimliModels.artalk if self.config.get("model") == "artalk" else SimliModels.fasttalk
        transport = (
            TransportMode.P2P
            if self.config.get("transport") == "p2p"
            else TransportMode.LIVEKIT
        )
        client_config = SimliConfig(
            faceId=str(self.config["face_id"]),
            handleSilence=bool(self.config.get("handle_silence", True)),
            maxSessionLength=int(self.config.get("max_session_length", 3600)),
            maxIdleTime=int(self.config.get("max_idle_time", 300)),
            model=model,
        )
        self.client = SimliClient(
            api_key=str(self.config["api_key"]),
            config=client_config,
            simliURL=str(self.config.get("api_base_url", "https://api.simli.ai")),
            retry_count=int(self.config.get("retry_count", 2)),
            retry_timeout=float(self.config.get("retry_timeout", 8.0)),
            transport_mode=transport,
        )
        try:
            await self.client.start()
            self.renderer = SimliLocalRenderer(
                self.client,
                window_title=str(self.config.get("window_title", "ALiver Simli Avatar")),
                window_size=list(self.config.get("window_size", [720, 720])),
                always_on_top=bool(self.config.get("always_on_top", False)),
                play_return_audio=bool(self.config.get("play_return_audio", True)),
                audio_output_device_index=self.config.get("audio_output_device_index"),
            )
            self.renderer_task = asyncio.create_task(
                self.renderer.render(), name=f"simli-render-{self.session_id}"
            )
            self.sender_task = asyncio.create_task(
                self._sender_loop(), name=f"simli-send-{self.session_id}"
            )
            loop = asyncio.get_running_loop()
            self.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(loop,),
                name=f"simli-gpt-out-{self.session_id}",
                daemon=True,
            )
            self.capture_thread.start()
            await asyncio.sleep(0.2)
            if self.state.get("error"):
                raise RuntimeError(str(self.state["error"]))
            await self.client.sendSilence(0.25)
            self.state.update({"status": "active", "started_at": utc_iso()})
            return self.status()
        except Exception as exc:
            self.state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            await self.stop()
            raise

    def _enqueue_audio(self, data: bytes) -> None:
        if not data or self.stop_flag.is_set():
            return
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
                self.state["dropped_chunks"] += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self.audio_queue.put_nowait(data)
        except asyncio.QueueFull:
            self.state["dropped_chunks"] += 1

    def _capture_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        pyaudio = None
        audio = None
        stream = None
        pending = bytearray()
        try:
            pyaudio = _load_pyaudio()
            audio = pyaudio.PyAudio()
            device_index = self.audio_manager._resolve_key("gpt_out")
            info = self.audio_manager._resolve_device(audio, device_index)
            channels = max(1, min(int(info.get("maxInputChannels") or 1), 2))
            sample_rate = int(float(info.get("defaultSampleRate") or 48000))
            converter = Pcm16ToSimliConverter(sample_rate, channels)
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                frames_per_buffer=1024,
                input=True,
                input_device_index=int(info["index"]),
            )
            self.state.update(
                {
                    "source_sample_rate": sample_rate,
                    "source_channels": channels,
                    "source_device": str(info.get("name", device_index)),
                }
            )
            while not self.stop_flag.is_set():
                raw = stream.read(1024, exception_on_overflow=False)
                self.state["last_input_dbfs"] = calculate_pcm16_levels(raw)["dbfs"]
                pending.extend(converter.convert(raw))
                while len(pending) >= SIMLI_CHUNK_BYTES:
                    chunk = bytes(pending[:SIMLI_CHUNK_BYTES])
                    del pending[:SIMLI_CHUNK_BYTES]
                    loop.call_soon_threadsafe(self._enqueue_audio, chunk)
            if pending:
                loop.call_soon_threadsafe(self._enqueue_audio, bytes(pending))
        except Exception as exc:
            self.state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            self.stop_flag.set()
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                audio.terminate()

    async def _sender_loop(self) -> None:
        try:
            while not self.stop_flag.is_set():
                try:
                    data = await asyncio.wait_for(self.audio_queue.get(), timeout=0.5)
                except TimeoutError:
                    if self.renderer and self.renderer.stop_event.is_set():
                        self.stop_flag.set()
                    continue
                if not self.client:
                    continue
                await self.client.send(data)
                self.state["sent_chunks"] += 1
                self.state["sent_bytes"] += len(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            self.stop_flag.set()

    async def stop(self) -> dict[str, Any]:
        self.stop_flag.set()
        if self.renderer is not None:
            self.renderer.stop_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            await asyncio.to_thread(self.capture_thread.join, 3.0)
        tasks = [task for task in (self.sender_task, self.renderer_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.client is not None:
            try:
                await self.client.stop()
            except Exception:
                pass
        if self.renderer is not None:
            await self.renderer.close()
        self.state["status"] = "ended" if not self.state.get("error") else "failed"
        self.state["stopped_at"] = utc_iso()
        return self.status()

    def status(self) -> dict[str, Any]:
        return dict(self.state)


class SimliSessionManager:
    def __init__(self, audio_manager: AudioCaptureManager) -> None:
        self.audio_manager = audio_manager
        self.sessions: dict[str, SimliRuntime] = {}
        self._lock = asyncio.Lock()

    async def start(self, session_id: str, config: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            active = [row for row in self.sessions.values() if row.state.get("status") == "active"]
            if active:
                raise RuntimeError("Only one active Simli session is supported on this Bridge.")
            runtime = SimliRuntime(session_id=session_id, config=config, audio_manager=self.audio_manager)
            self.sessions[session_id] = runtime
            try:
                return await runtime.start()
            except Exception:
                self.sessions.pop(session_id, None)
                raise

    async def stop(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            runtime = self.sessions.pop(session_id, None)
        if runtime is None:
            return {"session_id": session_id, "status": "ended", "already_stopped": True}
        return await runtime.stop()

    def status(self) -> dict[str, Any]:
        return {key: value.status() for key, value in self.sessions.items()}

    async def stop_all(self) -> None:
        async with self._lock:
            values = list(self.sessions.values())
            self.sessions.clear()
        for runtime in values:
            await runtime.stop()
