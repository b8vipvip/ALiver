from __future__ import annotations

import hmac
import json
import os
import queue
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from aiohttp import WSMsgType, web

from server.session_manager import session_manager
from utils.logger import logger

from .protocol import (
    BYTES_PER_FRAME,
    CHANNELS,
    FORMAT,
    FRAME_MS,
    PROTOCOL_VERSION,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    StartMessage,
)

REGISTRY_KEY = "aliver_pcm_stream_registry"
START_TIMEOUT_SECONDS = 10.0


@dataclass
class StreamMetrics:
    session_id: str
    stream_id: str
    connected_at: float
    last_activity_at: float
    epoch: int = 1
    received_bytes: int = 0
    received_frames: int = 0
    accepted_frames: int = 0
    dropped_frames: int = 0
    interrupts: int = 0
    queue_depth_frames: int = 0
    closed_at: float | None = None
    last_error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["connected_seconds"] = round(max(0.0, time.time() - self.connected_at), 3)
        value["queue_depth_ms"] = self.queue_depth_frames * FRAME_MS
        return value


def _json(data: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False))


def _expected_token() -> str:
    return os.environ.get("ALIVER_STREAM_TOKEN", "").strip()


def _allow_insecure() -> bool:
    return os.environ.get("ALIVER_ALLOW_INSECURE_PCM", "").strip().lower() in {"1", "true", "yes"}


def _authorized(request: web.Request) -> tuple[bool, str | None]:
    expected = _expected_token()
    if not expected:
        if _allow_insecure():
            return True, None
        return False, "ALIVER_STREAM_TOKEN 未配置，实时 PCM 接口已安全关闭"
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False, "缺少 Bearer Token"
    supplied = header[len(prefix) :].strip()
    if not hmac.compare_digest(supplied, expected):
        return False, "Bearer Token 无效"
    return True, None


def _max_queue_frames() -> int:
    try:
        milliseconds = int(os.environ.get("ALIVER_PCM_MAX_QUEUE_MS", "400"))
    except ValueError:
        milliseconds = 400
    milliseconds = max(100, min(milliseconds, 2000))
    return max(5, milliseconds // FRAME_MS)


def _drain(value: Any) -> int:
    if value is None or not hasattr(value, "get_nowait"):
        return 0
    count = 0
    while True:
        try:
            value.get_nowait()
            count += 1
        except queue.Empty:
            break
        except Exception:
            break
    return count


def _input_queue(avatar_session: Any) -> Any:
    return getattr(getattr(avatar_session, "asr", None), "queue", None)


def _flush_avatar(avatar_session: Any) -> dict[str, int]:
    try:
        avatar_session.flush_talk()
    except Exception:
        logger.exception("ALiver PCM: avatar flush_talk failed")
    asr = getattr(avatar_session, "asr", None)
    output = getattr(avatar_session, "output", None)
    player = getattr(output, "_player", None)
    cleared = {
        "input": _drain(getattr(asr, "queue", None)),
        "audio_output": _drain(getattr(asr, "output_queue", None)),
        "features": _drain(getattr(asr, "feat_queue", None)),
        "render": _drain(getattr(avatar_session, "res_frame_queue", None)),
    }
    if player is not None and hasattr(player, "clear_queues"):
        try:
            cleared.update({f"webrtc_{key}": value for key, value in player.clear_queues().items()})
        except Exception:
            logger.exception("ALiver PCM: WebRTC queue clear failed")
    return cleared


def _drop_oldest(avatar_session: Any, maximum: int) -> int:
    target = _input_queue(avatar_session)
    if target is None:
        return 0
    dropped = 0
    while target.qsize() >= maximum:
        try:
            target.get_nowait()
            dropped += 1
        except queue.Empty:
            break
    return dropped


def _queue_depth(avatar_session: Any) -> int:
    target = _input_queue(avatar_session)
    return int(target.qsize()) if target is not None else 0


def _put_pcm_frame(
    avatar_session: Any,
    frame_bytes: bytes,
    metrics: StreamMetrics,
    *,
    first: bool,
) -> None:
    if len(frame_bytes) != BYTES_PER_FRAME:
        raise ValueError(f"PCM 帧必须恰好为 {BYTES_PER_FRAME} 字节")
    samples = np.frombuffer(frame_bytes, dtype="<i2")
    if samples.size != SAMPLES_PER_FRAME:
        raise ValueError(f"PCM 帧必须包含 {SAMPLES_PER_FRAME} 个采样")
    dropped = _drop_oldest(avatar_session, _max_queue_frames())
    metrics.dropped_frames += dropped
    metadata = {
        "source": "aliver_pcm_websocket",
        "stream_id": metrics.stream_id,
        "epoch": metrics.epoch,
        "sequence": metrics.accepted_frames,
    }
    if first:
        metadata["status"] = "start"
    avatar_session.put_audio_frame(samples.astype(np.float32) / 32768.0, metadata)
    metrics.accepted_frames += 1
    metrics.queue_depth_frames = _queue_depth(avatar_session)


def _registry(app: web.Application) -> dict[str, StreamMetrics]:
    value = app.get(REGISTRY_KEY)
    if value is None:
        value = {}
        app[REGISTRY_KEY] = value
    return value


async def health(request: web.Request) -> web.Response:
    configured = bool(_expected_token()) or _allow_insecure()
    return _json(
        {
            "ok": True,
            "service": "livetalking-aliver-pcm",
            "protocol_version": PROTOCOL_VERSION,
            "authentication_configured": configured,
            "active_avatar_sessions": len(session_manager.sessions),
            "active_pcm_streams": len(_registry(request.app)),
            "audio": {
                "format": FORMAT,
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
                "frame_ms": FRAME_MS,
                "samples_per_frame": SAMPLES_PER_FRAME,
                "bytes_per_frame": BYTES_PER_FRAME,
            },
            "video_only_default": os.environ.get("ALIVER_VIDEO_ONLY", "1").strip().lower()
            not in {"0", "false", "no"},
        }
    )


async def stream_status(request: web.Request) -> web.Response:
    authorized, error = _authorized(request)
    if not authorized:
        return _json({"ok": False, "error": error}, status=401)
    return _json(
        {
            "ok": True,
            "streams": [item.public_dict() for item in _registry(request.app).values()],
        }
    )


async def pcm_websocket(request: web.Request) -> web.StreamResponse:
    authorized, error = _authorized(request)
    if not authorized:
        return _json({"ok": False, "error": error}, status=401)

    ws = web.WebSocketResponse(
        heartbeat=15.0,
        receive_timeout=45.0,
        max_msg_size=2 * 1024 * 1024,
        compress=False,
    )
    await ws.prepare(request)

    metrics: StreamMetrics | None = None
    avatar_session: Any = None
    accumulator = bytearray()
    first_frame = True
    registry = _registry(request.app)

    try:
        first_message = await ws.receive(timeout=START_TIMEOUT_SECONDS)
        if first_message.type != WSMsgType.TEXT:
            raise ValueError("首条 WebSocket 消息必须是 start JSON")
        start = StartMessage.from_payload(json.loads(first_message.data))
        avatar_session = session_manager.get_session(start.session_id)
        if avatar_session is None:
            raise ValueError(f"LiveTalking session 不存在：{start.session_id}")
        if start.stream_id in registry:
            raise ValueError(f"stream_id 已被占用：{start.stream_id}")

        now = time.time()
        metrics = StreamMetrics(
            session_id=start.session_id,
            stream_id=start.stream_id,
            connected_at=now,
            last_activity_at=now,
        )
        registry[start.stream_id] = metrics
        _flush_avatar(avatar_session)
        await ws.send_json({"type": "started", **start.public_dict(), "max_queue_ms": _max_queue_frames() * FRAME_MS})

        async for message in ws:
            metrics.last_activity_at = time.time()
            if message.type == WSMsgType.BINARY:
                chunk = bytes(message.data)
                metrics.received_bytes += len(chunk)
                accumulator.extend(chunk)
                while len(accumulator) >= BYTES_PER_FRAME:
                    frame = bytes(accumulator[:BYTES_PER_FRAME])
                    del accumulator[:BYTES_PER_FRAME]
                    metrics.received_frames += 1
                    _put_pcm_frame(avatar_session, frame, metrics, first=first_frame)
                    first_frame = False
                continue

            if message.type == WSMsgType.TEXT:
                payload = json.loads(message.data)
                command = str(payload.get("type") or "").strip().lower()
                if command == "ping":
                    metrics.queue_depth_frames = _queue_depth(avatar_session)
                    await ws.send_json({"type": "pong", "stream": metrics.public_dict()})
                elif command == "interrupt":
                    metrics.epoch += 1
                    metrics.interrupts += 1
                    accumulator.clear()
                    cleared = _flush_avatar(avatar_session)
                    first_frame = True
                    await ws.send_json({"type": "interrupted", "epoch": metrics.epoch, "cleared": cleared})
                elif command == "end":
                    if hasattr(avatar_session, "notify"):
                        avatar_session.notify({"status": "end", "stream_id": metrics.stream_id, "epoch": metrics.epoch})
                    await ws.send_json({"type": "ended", "stream": metrics.public_dict()})
                    await ws.close(code=1000, message=b"stream ended")
                    break
                elif command == "status":
                    metrics.queue_depth_frames = _queue_depth(avatar_session)
                    await ws.send_json({"type": "status", "stream": metrics.public_dict()})
                else:
                    await ws.send_json({"type": "error", "error": f"未知控制消息：{command}"})
                continue

            if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break
    except Exception as exc:
        logger.exception("ALiver PCM WebSocket failed")
        if metrics is not None:
            metrics.last_error = f"{type(exc).__name__}: {exc}"
        if not ws.closed:
            await ws.send_json({"type": "error", "error": str(exc)})
            await ws.close(code=1008, message=str(exc).encode("utf-8", errors="ignore")[:120])
    finally:
        if metrics is not None:
            metrics.closed_at = time.time()
            metrics.queue_depth_frames = _queue_depth(avatar_session)
            registry.pop(metrics.stream_id, None)
        if avatar_session is not None and metrics is not None and hasattr(avatar_session, "notify"):
            try:
                avatar_session.notify({"status": "end", "stream_id": metrics.stream_id, "epoch": metrics.epoch})
            except Exception:
                logger.exception("ALiver PCM end notification failed")
    return ws


def setup_aliver_routes(app: web.Application) -> None:
    app.router.add_get("/api/aliver/health", health)
    app.router.add_get("/api/aliver/streams", stream_status)
    app.router.add_get("/api/aliver/pcm", pcm_websocket)
    logger.info(
        "ALiver PCM routes enabled: 16kHz mono s16le, %dms/%d-byte frames, video-only default=%s",
        FRAME_MS,
        BYTES_PER_FRAME,
        os.environ.get("ALIVER_VIDEO_ONLY", "1"),
    )
