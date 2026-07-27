from __future__ import annotations

import asyncio
import audioop
import contextlib
import importlib.metadata
import importlib.util
import io
import platform
import sys
import threading
import traceback
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

PHASE_LABELS = {
    "preflight": "运行环境预检",
    "sdk_import": "加载 Simli SDK",
    "sdk_initialize": "初始化 Simli 实时连接",
    "renderer_initialize": "初始化数字人窗口和返回音频",
    "audio_capture": "打开 GPT_OUT 音频捕获",
    "streaming": "实时音视频传输",
    "stopping": "释放 Simli 会话资源",
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def exception_chain(exc: BaseException) -> list[str]:
    values: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return values


def classify_simli_failure(
    exc: BaseException,
    *,
    phase: str,
    diagnostics: dict[str, Any],
    sdk_stderr: str = "",
) -> dict[str, Any]:
    chain = exception_chain(exc)
    combined = "\n".join(chain + [sdk_stderr]).lower()
    code = "SIMLI_INITIALIZATION_FAILED"
    message_zh = "Simli 实时数字人初始化失败。"
    suggestions = [
        "确认 Simli API Key 和 Face ID 仍然有效。",
        "确认电脑能正常访问 api.simli.ai，并检查代理、防火墙或安全软件。",
        "重新安装项目依赖后重启 Bridge，再启动会话。",
    ]

    if "livekit not installed" in combined or (
        diagnostics.get("transport") == "livekit" and not diagnostics.get("livekit_module_available")
    ):
        code = "SIMLI_LIVEKIT_DEPENDENCY_MISSING"
        message_zh = "缺少 Simli 的 LiveKit 传输依赖，无法建立实时数字人连接。"
        suggestions = [
            '在 D:\\AI\\ALiver 执行：.\\.venv\\Scripts\\python.exe -m pip install -U "simli-ai[livekit]>=2.0.3,<3.0"',
            "安装完成后关闭并重新启动 Bridge PowerShell 窗口。",
            "再次启动 Simli 会话；不需要重新创建供应商。",
        ]
    elif "no module named" in combined:
        code = "SIMLI_DEPENDENCY_MISSING"
        message_zh = "Simli 运行依赖缺失或安装不完整。"
        suggestions = [
            "在项目目录重新执行：.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            "执行完成后重启 Bridge。",
        ]
    elif "401" in combined or "unauthorized" in combined or "invalid api" in combined:
        code = "SIMLI_AUTH_FAILED"
        message_zh = "Simli 身份验证失败，API Key 可能无效或已被撤销。"
        suggestions = ["到 Simli Studio 的 API keys 页面重新创建密钥，并更新 ALiver 供应商凭据。"]
    elif "403" in combined or "forbidden" in combined:
        code = "SIMLI_PERMISSION_DENIED"
        message_zh = "Simli 拒绝了会话请求，当前账号、Face ID 或套餐权限可能不允许启动该会话。"
        suggestions = ["检查 Face ID 是否属于当前账号，并查看 Simli 剩余额度和账号状态。"]
    elif "face" in combined and ("invalid" in combined or "not found" in combined):
        code = "SIMLI_FACE_INVALID"
        message_zh = "Simli Face ID 无效、不可用或不属于当前账号。"
        suggestions = ["从 Simli Studio 的 Your Faces 页面重新复制 Face ID，再更新供应商设置。"]
    elif "getaddrinfo" in combined or "name or service not known" in combined or "dns" in combined:
        code = "SIMLI_DNS_FAILED"
        message_zh = "无法解析 Simli 服务域名，当前网络或 DNS 配置异常。"
        suggestions = ["检查网络、DNS、VPN 和代理设置，确认浏览器能访问 Simli Studio。"]
    elif "ssl" in combined or "certificate" in combined:
        code = "SIMLI_TLS_FAILED"
        message_zh = "连接 Simli 时 TLS/证书校验失败。"
        suggestions = ["检查系统时间、代理证书和安全软件的 HTTPS 扫描功能。"]
    elif "timeout" in combined or "timed out" in combined:
        code = "SIMLI_TIMEOUT"
        message_zh = "连接 Simli 超时，网络延迟过高或实时服务未及时响应。"
        suggestions = ["关闭代理后重试，或把供应商 transport 临时改为 p2p 对比测试。"]
    elif "websocket" in combined or "unable to connect to simli" in combined:
        code = "SIMLI_REALTIME_CONNECTION_FAILED"
        message_zh = "Simli 实时 WebSocket/WebRTC 连接建立失败。"
        suggestions = [
            "检查防火墙、VPN、代理是否拦截 WebSocket 或 WebRTC。",
            "先保持 transport=livekit；若仍失败，可临时改成 p2p 对比测试。",
        ]
    elif "gpt_out" in combined or "audio" in combined and phase == "audio_capture":
        code = "SIMLI_GPT_OUT_FAILED"
        message_zh = "Simli 已连接，但打开 GPT_OUT 音频捕获失败。"
        suggestions = [
            "确认音频路由页面的 GPT_OUT 已保存并且 Bridge 在线。",
            "停止正在运行的 10 秒 GPT_OUT 测试，再启动 Simli 会话。",
        ]

    return {
        "code": code,
        "message_zh": message_zh,
        "phase": phase,
        "phase_zh": PHASE_LABELS.get(phase, phase),
        "original_error": chain[0] if chain else f"{type(exc).__name__}: {exc}",
        "exception_chain": chain,
        "suggestions": suggestions,
        "diagnostics": diagnostics,
        "sdk_stderr": sdk_stderr[-8000:] if sdk_stderr else "",
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-12000:],
        "occurred_at": utc_iso(),
    }


class SimliRuntimeError(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        lines = [
            str(detail.get("message_zh") or "Simli 会话失败"),
            f"错误代码：{detail.get('code', 'SIMLI_UNKNOWN')}",
            f"失败阶段：{detail.get('phase_zh', detail.get('phase', '未知'))}",
            f"原始异常：{detail.get('original_error', '无')}",
        ]
        suggestions = detail.get("suggestions") or []
        if suggestions:
            lines.append("处理建议：")
            lines.extend(f"{index + 1}. {value}" for index, value in enumerate(suggestions))
        super().__init__("\n".join(lines))


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
            raise RuntimeError("缺少 opencv-python，无法显示 Simli 数字人窗口。请重新安装 requirements.txt。") from exc

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
            "phase": "preflight",
            "phase_zh": PHASE_LABELS["preflight"],
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
            "error_detail": None,
            "diagnostics": {},
        }

    def _set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self.state["phase_zh"] = PHASE_LABELS.get(phase, phase)

    def _dependency_diagnostics(self) -> dict[str, Any]:
        transport = str(self.config.get("transport") or "livekit").lower()
        route_status: dict[str, Any]
        try:
            route_status = self.audio_manager.get_routes()
        except Exception as exc:
            route_status = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
        values = {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "transport": transport,
            "simli_ai_version": package_version("simli-ai"),
            "livekit_version": package_version("livekit"),
            "livekit_api_version": package_version("livekit-api"),
            "aiortc_version": package_version("aiortc"),
            "av_version": package_version("av"),
            "websockets_version": package_version("websockets"),
            "opencv_version": package_version("opencv-python"),
            "pyaudio_wpatch_version": package_version("PyAudioWPatch"),
            "livekit_module_available": importlib.util.find_spec("livekit") is not None,
            "gpt_out_route_ready": bool(route_status.get("gpt_out", {}).get("ready")),
            "gpt_out_device": (route_status.get("gpt_out", {}).get("capture") or {}).get("name"),
        }
        self.state["diagnostics"] = values
        return values

    async def start(self) -> dict[str, Any]:
        if self.audio_manager.status().get("active"):
            detail = classify_simli_failure(
                RuntimeError("GPT_OUT capture test is active"),
                phase="audio_capture",
                diagnostics=self._dependency_diagnostics(),
            )
            detail["message_zh"] = "GPT_OUT 10 秒捕获测试仍在运行，Simli 无法同时占用该音频设备。"
            detail["suggestions"] = ["先在音频路由页面点击“立即停止”，再启动 Simli 会话。"]
            raise SimliRuntimeError(detail)

        self._set_phase("preflight")
        diagnostics = self._dependency_diagnostics()
        if not diagnostics.get("simli_ai_version"):
            raise SimliRuntimeError(
                classify_simli_failure(
                    ModuleNotFoundError("No module named 'simli'"),
                    phase="sdk_import",
                    diagnostics=diagnostics,
                )
            )
        if diagnostics.get("transport") == "livekit" and not diagnostics.get("livekit_module_available"):
            raise SimliRuntimeError(
                classify_simli_failure(
                    RuntimeError("livekit not installed"),
                    phase="sdk_import",
                    diagnostics=diagnostics,
                )
            )
        if not diagnostics.get("gpt_out_route_ready"):
            detail = classify_simli_failure(
                RuntimeError("GPT_OUT route is not configured or the device is unavailable"),
                phase="audio_capture",
                diagnostics=diagnostics,
            )
            detail["message_zh"] = "GPT_OUT 路由未就绪，Bridge 无法捕获 ChatGPT 的回答音频。"
            detail["suggestions"] = ["进入“音频路由”页面重新扫描、选择并保存 GPT_OUT。"]
            raise SimliRuntimeError(detail)

        self._set_phase("sdk_import")
        try:
            from simli import SimliClient, SimliConfig
            from simli.simli import SimliModels, TransportMode
        except ImportError as exc:
            raise SimliRuntimeError(
                classify_simli_failure(exc, phase="sdk_import", diagnostics=diagnostics)
            ) from exc

        model = SimliModels.artalk if self.config.get("model") == "artalk" else SimliModels.fasttalk
        transport = (
            TransportMode.P2P if self.config.get("transport") == "p2p" else TransportMode.LIVEKIT
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

        sdk_stderr = io.StringIO()
        try:
            self._set_phase("sdk_initialize")
            with contextlib.redirect_stderr(sdk_stderr):
                await self.client.start()

            self._set_phase("renderer_initialize")
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

            self._set_phase("audio_capture")
            loop = asyncio.get_running_loop()
            self.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(loop,),
                name=f"simli-gpt-out-{self.session_id}",
                daemon=True,
            )
            self.capture_thread.start()
            await asyncio.sleep(0.25)
            if self.state.get("error_detail"):
                raise SimliRuntimeError(dict(self.state["error_detail"]))
            if self.state.get("error"):
                raise RuntimeError(str(self.state["error"]))

            await self.client.sendSilence(0.25)
            self._set_phase("streaming")
            self.state.update({"status": "active", "started_at": utc_iso()})
            return self.status()
        except SimliRuntimeError:
            await self.stop()
            raise
        except Exception as exc:
            detail = classify_simli_failure(
                exc,
                phase=str(self.state.get("phase") or "sdk_initialize"),
                diagnostics=diagnostics,
                sdk_stderr=sdk_stderr.getvalue(),
            )
            self.state.update(
                {
                    "status": "failed",
                    "error": detail["message_zh"],
                    "error_detail": detail,
                }
            )
            await self.stop()
            raise SimliRuntimeError(detail) from exc

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
            detail = classify_simli_failure(
                exc,
                phase="audio_capture",
                diagnostics=dict(self.state.get("diagnostics") or {}),
            )
            self.state.update(
                {"status": "failed", "error": detail["message_zh"], "error_detail": detail}
            )
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
            detail = classify_simli_failure(
                exc,
                phase="streaming",
                diagnostics=dict(self.state.get("diagnostics") or {}),
            )
            self.state.update(
                {"status": "failed", "error": detail["message_zh"], "error_detail": detail}
            )
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
                raise RuntimeError("当前 Bridge 已有一个活动的 Simli 会话，请先停止旧会话。")
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
