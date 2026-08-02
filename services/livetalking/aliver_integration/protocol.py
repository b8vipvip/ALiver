from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
SAMPLE_RATE = 16_000
CHANNELS = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000
BYTES_PER_SAMPLE = 2
BYTES_PER_FRAME = SAMPLES_PER_FRAME * BYTES_PER_SAMPLE
FORMAT = "s16le"


@dataclass(frozen=True)
class StartMessage:
    session_id: str
    stream_id: str
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    frame_ms: int = FRAME_MS
    audio_format: str = FORMAT

    @classmethod
    def from_payload(cls, payload: Any) -> "StartMessage":
        if not isinstance(payload, dict) or payload.get("type") != "start":
            raise ValueError("首条消息必须是 type=start 的 JSON 控制帧")
        value = cls(
            session_id=str(
                payload.get("session_id") or payload.get("sessionid") or ""
            ).strip(),
            stream_id=str(payload.get("stream_id") or "").strip(),
            sample_rate=int(payload.get("sample_rate") or SAMPLE_RATE),
            channels=int(payload.get("channels") or CHANNELS),
            frame_ms=int(payload.get("frame_ms") or FRAME_MS),
            audio_format=str(payload.get("format") or FORMAT).strip().lower(),
        )
        if not value.session_id:
            raise ValueError("session_id 不能为空")
        if not value.stream_id:
            raise ValueError("stream_id 不能为空")
        if value.sample_rate != SAMPLE_RATE:
            raise ValueError(f"仅支持 {SAMPLE_RATE} Hz PCM")
        if value.channels != CHANNELS:
            raise ValueError("仅支持单声道 PCM")
        if value.frame_ms != FRAME_MS:
            raise ValueError(f"仅支持 {FRAME_MS} ms 音频帧")
        if value.audio_format != FORMAT:
            raise ValueError(f"仅支持 {FORMAT} little-endian PCM")
        return value

    def public_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "stream_id": self.stream_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_ms": self.frame_ms,
            "format": self.audio_format,
            "samples_per_frame": SAMPLES_PER_FRAME,
            "bytes_per_frame": BYTES_PER_FRAME,
        }
