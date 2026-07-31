from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from bridge import audio_capture

TTS_CACHE_DIR = Path(__file__).resolve().parent / "captures" / "tts"


def _resolve_gpt_out_playback(manager: Any) -> tuple[int, dict[str, Any]]:
    scanned = manager.list_devices()
    key = (manager._routes.get("gpt_out") or {}).get("playback_device_key")
    row = manager._find_by_key(scanned["output_devices"], key)
    if not row:
        raise RuntimeError("GPT_OUT 虚拟扬声器尚未配置或设备不可用")
    return int(row["index"]), row


def _scale_pcm16(data: bytes, volume: float) -> bytes:
    if abs(volume - 1.0) < 0.001:
        return data
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    for index, value in enumerate(samples):
        samples[index] = max(-32768, min(32767, int(value * volume)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def play_gpt_out_wav(manager: Any, path: str | Path, *, volume: float = 1.0) -> dict[str, Any]:
    wav_path = Path(path)
    if not wav_path.exists() or not wav_path.is_file():
        raise RuntimeError(f"TTS WAV 文件不存在：{wav_path}")
    volume = max(0.05, min(float(volume), 2.0))
    device_index, device = _resolve_gpt_out_playback(manager)
    pyaudio = audio_capture._load_pyaudio()
    audio = pyaudio.PyAudio()
    stream = None
    started = time.monotonic()
    frames = 0
    try:
        with wave.open(str(wav_path), "rb") as reader:
            sample_width = reader.getsampwidth()
            if sample_width != 2:
                raise RuntimeError(f"当前仅支持 PCM16 WAV，实际采样宽度为 {sample_width * 8} bit")
            channels = int(reader.getnchannels())
            sample_rate = int(reader.getframerate())
            if channels < 1 or channels > 2:
                raise RuntimeError(f"当前仅支持单声道或双声道 WAV，实际为 {channels} 声道")
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=2048,
            )
            while True:
                data = reader.readframes(2048)
                if not data:
                    break
                stream.write(_scale_pcm16(data, volume))
                frames += len(data) // (sample_width * channels)
        return {
            "played": True,
            "path": str(wav_path),
            "device": device,
            "sample_rate": sample_rate,
            "channels": channels,
            "frames": frames,
            "duration_seconds": round(frames / max(1, sample_rate), 3),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "volume": volume,
        }
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
        audio.terminate()


async def download_and_play_tts(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    audio_path = str(payload.get("audio_path") or "").strip()
    if not audio_path.startswith("/api/voice/audio/"):
        raise ValueError("只允许下载 ALiver 服务端签发的临时语音文件")
    url = f"{agent.server_url.rstrip('/')}{audio_path}"
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = TTS_CACHE_DIR / f"tts-{uuid4().hex}.wav"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            local_path.write_bytes(response.content)
        result = await asyncio.to_thread(
            play_gpt_out_wav,
            agent.audio,
            local_path,
            volume=float(payload.get("volume") or 1.0),
        )
        result["source"] = str(payload.get("source") or "api_tts")
        return result
    finally:
        if bool(payload.get("delete_after", True)):
            try:
                os.remove(local_path)
            except OSError:
                pass
