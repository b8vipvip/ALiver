from __future__ import annotations

import asyncio
import json
import queue
import ssl
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "livetalking_cloud.local.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "ws_url": "",
    "token": "",
    "session_id": "",
    "verify_tls": True,
    "max_queue_ms": 400,
    "reconnect_min_seconds": 1.0,
    "reconnect_max_seconds": 15.0,
}

TARGET_RATE = 16_000
FRAME_MS = 20
FRAME_SAMPLES = TARGET_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def normalize_config(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_CONFIG)
    result["enabled"] = bool(source.get("enabled", result["enabled"]))
    result["verify_tls"] = bool(source.get("verify_tls", result["verify_tls"]))
    for key in ("ws_url", "token", "session_id"):
        result[key] = str(source.get(key, result[key]) or "").strip()
    try:
        result["max_queue_ms"] = max(100, min(int(source.get("max_queue_ms", 400)), 2000))
    except (TypeError, ValueError):
        result["max_queue_ms"] = 400
    for key, default, minimum, maximum in (
        ("reconnect_min_seconds", 1.0, 0.5, 30.0),
        ("reconnect_max_seconds", 15.0, 1.0, 120.0),
    ):
        try:
            result[key] = max(minimum, min(float(source.get(key, default)), maximum))
        except (TypeError, ValueError):
            result[key] = default
    result["reconnect_max_seconds"] = max(
        result["reconnect_min_seconds"], result["reconnect_max_seconds"]
    )
    return result


class StreamingLinearResampler:
    """Small stateful resampler suitable for non-blocking lip-sync PCM copies."""

    def __init__(self, target_rate: int = TARGET_RATE) -> None:
        self.target_rate = int(target_rate)
        self.source_rate: int | None = None
        self.buffer = np.zeros(0, dtype=np.float32)
        self.position = 0.0

    def reset(self) -> None:
        self.source_rate = None
        self.buffer = np.zeros(0, dtype=np.float32)
        self.position = 0.0

    def process(self, samples: np.ndarray, source_rate: int) -> np.ndarray:
        value = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not value.size:
            return np.zeros(0, dtype=np.float32)
        source_rate = max(1, int(source_rate))
        if self.source_rate != source_rate:
            self.reset()
            self.source_rate = source_rate
        self.buffer = np.concatenate((self.buffer, value))
        step = source_rate / self.target_rate
        if self.buffer.size < 2:
            return np.zeros(0, dtype=np.float32)
        positions = np.arange(self.position, self.buffer.size - 1, step, dtype=np.float64)
        if not positions.size:
            return np.zeros(0, dtype=np.float32)
        left = np.floor(positions).astype(np.int64)
        fraction = positions - left
        output = self.buffer[left] * (1.0 - fraction) + self.buffer[left + 1] * fraction
        next_position = float(positions[-1] + step)
        consumed = min(int(next_position), max(0, self.buffer.size - 1))
        if consumed:
            self.buffer = self.buffer[consumed:]
            next_position -= consumed
        self.position = next_position
        return np.ascontiguousarray(output, dtype=np.float32)


class LiveTalkingPCMClient:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._audio_lock = threading.Lock()
        self._config = self._load_config()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=self._queue_capacity())
        self._resampler = StreamingLinearResampler()
        self._pending = np.zeros(0, dtype=np.float32)
        self._state: dict[str, Any] = {
            "status": "stopped",
            "running": False,
            "connected": False,
            "connected_at": None,
            "last_connected_at": None,
            "last_disconnected_at": None,
            "last_error": None,
            "last_server_message": None,
            "stream_id": None,
            "frames_queued": 0,
            "frames_sent": 0,
            "frames_dropped": 0,
            "bytes_sent": 0,
            "reconnects": 0,
            "source_sample_rate": None,
        }

    def _load_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return normalize_config(DEFAULT_CONFIG)
        try:
            return normalize_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return normalize_config(DEFAULT_CONFIG)

    def _save_config(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _queue_capacity(self) -> int:
        return max(5, int(self._config.get("max_queue_ms") or 400) // FRAME_MS)

    def _rebuild_queue(self) -> None:
        old = self._queue
        self._queue = queue.Queue(maxsize=self._queue_capacity())
        while not old.empty() and not self._queue.full():
            try:
                self._queue.put_nowait(old.get_nowait())
            except queue.Empty:
                break

    def configure(self, values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        with self._lock:
            previous = dict(self._config)
            self._config = normalize_config({**self._config, **dict(values or {})})
            if persist:
                self._save_config()
            if previous.get("max_queue_ms") != self._config.get("max_queue_ms"):
                self._rebuild_queue()
            restart = any(
                previous.get(key) != self._config.get(key)
                for key in ("ws_url", "token", "session_id", "verify_tls")
            )
            running = bool(self._thread and self._thread.is_alive())
        if restart and running:
            self.stop(persist_disable=False)
            if self._config.get("enabled"):
                return self.start()
        return self.status()

    def _validate(self) -> None:
        url = str(self._config.get("ws_url") or "")
        if not (url.startswith("ws://") or url.startswith("wss://")):
            raise RuntimeError("LiveTalking ws_url 必须以 ws:// 或 wss:// 开头")
        if not self._config.get("session_id"):
            raise RuntimeError("请先创建 LiveTalking WebRTC 会话并填写 session_id")
        if not self._config.get("token"):
            raise RuntimeError("LiveTalking 实时 PCM Token 不能为空")

    def start(self) -> dict[str, Any]:
        self._validate()
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._config["enabled"] = True
            self._save_config()
            self._stop.clear()
            self._state.update({"status": "connecting", "running": True, "last_error": None})
            self._thread = threading.Thread(
                target=self._thread_main,
                name="aliver-livetalking-pcm",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def autostart(self) -> dict[str, Any]:
        if self._config.get("enabled"):
            try:
                return self.start()
            except Exception as exc:
                with self._lock:
                    self._state.update(
                        {"status": "failed", "running": False, "last_error": f"{type(exc).__name__}: {exc}"}
                    )
        return self.status()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._supervisor())
        except Exception as exc:
            with self._lock:
                self._state.update(
                    {"status": "failed", "running": False, "connected": False, "last_error": f"{type(exc).__name__}: {exc}"}
                )

    def _ssl_context(self, url: str) -> ssl.SSLContext | None:
        if not url.startswith("wss://"):
            return None
        if bool(self._config.get("verify_tls", True)):
            return ssl.create_default_context()
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    async def _supervisor(self) -> None:
        delay = float(self._config["reconnect_min_seconds"])
        while not self._stop.is_set():
            try:
                await self._connect_once()
                delay = float(self._config["reconnect_min_seconds"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._state.update(
                        {
                            "status": "reconnecting" if not self._stop.is_set() else "stopped",
                            "connected": False,
                            "last_disconnected_at": _now_iso(),
                            "last_error": f"{type(exc).__name__}: {exc}",
                            "reconnects": int(self._state.get("reconnects") or 0) + 1,
                        }
                    )
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2.0, float(self._config["reconnect_max_seconds"]))
        with self._lock:
            self._state.update({"status": "stopped", "running": False, "connected": False})

    async def _connect_once(self) -> None:
        from websockets.asyncio.client import connect

        config = dict(self._config)
        stream_id = f"aliver-{uuid.uuid4().hex}"
        headers = {"Authorization": f"Bearer {config['token']}"}
        async with connect(
            config["ws_url"],
            additional_headers=headers,
            ssl=self._ssl_context(config["ws_url"]),
            open_timeout=12,
            close_timeout=5,
            ping_interval=15,
            ping_timeout=10,
            max_size=2 * 1024 * 1024,
            compression=None,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "start",
                        "session_id": config["session_id"],
                        "stream_id": stream_id,
                        "format": "s16le",
                        "sample_rate": TARGET_RATE,
                        "channels": 1,
                        "frame_ms": FRAME_MS,
                    }
                )
            )
            first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=12.0))
            if first.get("type") != "started":
                raise RuntimeError(str(first.get("error") or f"LiveTalking 拒绝流：{first}"))
            with self._lock:
                self._state.update(
                    {
                        "status": "connected",
                        "running": True,
                        "connected": True,
                        "connected_at": _now_iso(),
                        "last_connected_at": _now_iso(),
                        "last_error": None,
                        "stream_id": stream_id,
                    }
                )
            receiver = asyncio.create_task(self._receive_loop(websocket), name="livetalking-pcm-receiver")
            try:
                await self._send_loop(websocket)
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

    async def _receive_loop(self, websocket: Any) -> None:
        async for message in websocket:
            if isinstance(message, bytes):
                continue
            try:
                value = json.loads(message)
            except ValueError:
                value = {"type": "text", "value": str(message)}
            with self._lock:
                self._state["last_server_message"] = value
                if value.get("type") == "error":
                    self._state["last_error"] = str(value.get("error") or value)

    async def _send_loop(self, websocket: Any) -> None:
        while not self._stop.is_set():
            try:
                kind, payload = await asyncio.to_thread(self._queue.get, True, 0.25)
            except queue.Empty:
                continue
            if kind == "audio":
                await websocket.send(payload)
                with self._lock:
                    self._state["frames_sent"] = int(self._state.get("frames_sent") or 0) + 1
                    self._state["bytes_sent"] = int(self._state.get("bytes_sent") or 0) + len(payload)
            elif kind == "interrupt":
                await websocket.send(json.dumps({"type": "interrupt"}))
            elif kind == "stop":
                await websocket.send(json.dumps({"type": "end"}))
                return
            with self._lock:
                self._state["frames_queued"] = self._queue.qsize()

    def _enqueue(self, item: tuple[str, Any]) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self._state["frames_dropped"] = int(self._state.get("frames_dropped") or 0) + 1
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                pass
        with self._lock:
            self._state["frames_queued"] = self._queue.qsize()

    def feed(self, samples: np.ndarray, source_rate: int) -> None:
        if not (self._thread and self._thread.is_alive()) or self._stop.is_set():
            return
        value = np.asarray(samples, dtype=np.float32)
        if value.ndim == 2:
            mono = np.mean(value, axis=0, dtype=np.float32)
        else:
            mono = value.reshape(-1)
        with self._audio_lock:
            rendered = self._resampler.process(mono, int(source_rate))
            if rendered.size:
                self._pending = np.concatenate((self._pending, rendered))
            while self._pending.size >= FRAME_SAMPLES:
                frame = self._pending[:FRAME_SAMPLES]
                self._pending = self._pending[FRAME_SAMPLES:]
                pcm = (np.clip(frame, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                if len(pcm) == FRAME_BYTES:
                    self._enqueue(("audio", pcm))
        with self._lock:
            self._state["source_sample_rate"] = int(source_rate)

    def interrupt(self) -> dict[str, Any]:
        with self._audio_lock:
            self._resampler.reset()
            self._pending = np.zeros(0, dtype=np.float32)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._enqueue(("interrupt", None))
        return self.status()

    def stop(self, *, persist_disable: bool = True) -> dict[str, Any]:
        if persist_disable:
            with self._lock:
                self._config["enabled"] = False
                self._save_config()
        self._enqueue(("stop", None))
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=6.0)
        with self._lock:
            self._state.update({"status": "stopped", "running": False, "connected": False})
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = dict(self._config)
            config["token_configured"] = bool(config.pop("token", ""))
            return {
                **dict(self._state),
                "running": bool(self._thread and self._thread.is_alive() and self._state.get("running")),
                "config": config,
                "protocol": {
                    "format": "s16le",
                    "sample_rate": TARGET_RATE,
                    "channels": 1,
                    "frame_ms": FRAME_MS,
                    "frame_bytes": FRAME_BYTES,
                },
            }

    def shutdown(self) -> None:
        self.stop(persist_disable=False)
