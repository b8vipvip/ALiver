from __future__ import annotations

import math
import os
import sys
import threading
import time
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CAPTURE_DIR = BASE_DIR / "captures"


def calculate_pcm16_levels(data: bytes) -> dict[str, float]:
    """Return RMS, peak and dBFS for little-endian signed PCM16 data."""
    if not data:
        return {"rms": 0.0, "peak": 0.0, "dbfs": -96.0}

    samples = array("h")
    usable = len(data) - (len(data) % 2)
    samples.frombytes(data[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return {"rms": 0.0, "peak": 0.0, "dbfs": -96.0}

    peak = max(abs(sample) for sample in samples)
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    rms = math.sqrt(mean_square)
    dbfs = 20.0 * math.log10(rms / 32768.0) if rms > 0 else -96.0
    return {
        "rms": round(rms, 2),
        "peak": round(float(peak), 2),
        "dbfs": round(max(dbfs, -96.0), 2),
    }


def _load_pyaudio():
    if os.name != "nt":
        raise RuntimeError("WASAPI audio capture is available only on Windows.")
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "PyAudioWPatch is not installed. Run "
            r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        ) from exc
    return pyaudio


class AudioCaptureManager:
    """Owns one WASAPI loopback/input capture stream for the local Bridge."""

    def __init__(self, capture_dir: Path | None = None) -> None:
        self.capture_dir = capture_dir or CAPTURE_DIR
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._startup_event = threading.Event()
        self._startup_error: str | None = None
        self._started_monotonic: float | None = None
        self._state: dict[str, Any] = self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "active": False,
            "device": None,
            "sample_rate": None,
            "channels": None,
            "chunk_size": None,
            "rms": 0.0,
            "peak": 0.0,
            "dbfs": -96.0,
            "captured_seconds": 0.0,
            "frames_captured": 0,
            "wav_path": None,
            "wav_complete": False,
            "started_at": None,
            "stopped_at": None,
            "error": None,
        }

    @staticmethod
    def _device_payload(info: dict[str, Any], defaults: dict[str, int | None]) -> dict[str, Any]:
        index = int(info["index"])
        input_channels = int(info.get("maxInputChannels") or 0)
        output_channels = int(info.get("maxOutputChannels") or 0)
        is_loopback = bool(info.get("isLoopbackDevice", False))
        if is_loopback:
            kind = "loopback"
        elif input_channels > 0:
            kind = "input"
        else:
            kind = "output"
        return {
            "index": index,
            "name": str(info.get("name", f"Device {index}")),
            "kind": kind,
            "is_loopback": is_loopback,
            "input_channels": input_channels,
            "output_channels": output_channels,
            "default_sample_rate": int(float(info.get("defaultSampleRate") or 0)),
            "is_default_input": index == defaults.get("input"),
            "is_default_output": index == defaults.get("output"),
            "is_default_loopback": index == defaults.get("loopback"),
        }

    def list_devices(self) -> dict[str, Any]:
        pyaudio = _load_pyaudio()
        audio = pyaudio.PyAudio()
        try:
            try:
                wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            except OSError as exc:
                raise RuntimeError("WASAPI is not available on this Windows computer.") from exc

            defaults: dict[str, int | None] = {
                "input": int(wasapi.get("defaultInputDevice", -1)),
                "output": int(wasapi.get("defaultOutputDevice", -1)),
                "loopback": None,
            }
            if defaults["input"] is not None and defaults["input"] < 0:
                defaults["input"] = None
            if defaults["output"] is not None and defaults["output"] < 0:
                defaults["output"] = None

            try:
                loopback = audio.get_default_wasapi_loopback()
                defaults["loopback"] = int(loopback["index"])
            except (AttributeError, OSError):
                defaults["loopback"] = None

            host_index = int(wasapi["index"])
            devices: list[dict[str, Any]] = []
            for info in audio.get_device_info_generator():
                if int(info.get("hostApi", -1)) != host_index:
                    continue
                devices.append(self._device_payload(info, defaults))

            capture_devices = [
                row for row in devices if row["input_channels"] > 0
            ]
            output_devices = [
                row for row in devices if row["output_channels"] > 0 and not row["is_loopback"]
            ]
            capture_devices.sort(
                key=lambda row: (
                    not row["is_default_loopback"],
                    row["kind"] != "loopback",
                    row["name"].lower(),
                )
            )
            output_devices.sort(key=lambda row: (not row["is_default_output"], row["name"].lower()))
            return {
                "backend": "PyAudioWPatch/WASAPI",
                "capture_devices": capture_devices,
                "output_devices": output_devices,
                "defaults": defaults,
            }
        finally:
            audio.terminate()

    def _resolve_device(self, audio, pyaudio, device_index: int | None) -> dict[str, Any]:
        if device_index is None:
            try:
                return dict(audio.get_default_wasapi_loopback())
            except (AttributeError, OSError) as exc:
                raise RuntimeError(
                    "Default WASAPI loopback device was not found. Scan devices and select one."
                ) from exc

        info = dict(audio.get_device_info_by_index(int(device_index)))
        if int(info.get("maxInputChannels") or 0) > 0:
            return info

        try:
            return dict(audio.get_wasapi_loopback_analogue_by_index(int(device_index)))
        except (AttributeError, OSError) as exc:
            raise RuntimeError(
                "The selected device cannot be opened as an input/loopback device."
            ) from exc

    def start(
        self,
        device_index: int | None = None,
        *,
        chunk_size: int = 1024,
        save_wav: bool = True,
        wav_seconds: float = 10.0,
    ) -> dict[str, Any]:
        chunk_size = max(128, min(int(chunk_size), 8192))
        wav_seconds = max(1.0, min(float(wav_seconds), 60.0))

        with self._lock:
            if self._thread and self._thread.is_alive():
                result = self.status()
                result["already_running"] = True
                return result
            self._stop_event.clear()
            self._startup_event.clear()
            self._startup_error = None
            self._started_monotonic = None
            self._state = self._empty_state()
            self._thread = threading.Thread(
                target=self._capture_worker,
                args=(device_index, chunk_size, save_wav, wav_seconds),
                name="aliver-audio-capture",
                daemon=True,
            )
            self._thread.start()

        if not self._startup_event.wait(timeout=8):
            self._stop_event.set()
            raise RuntimeError("Audio capture did not start within 8 seconds.")
        if self._startup_error:
            raise RuntimeError(self._startup_error)
        return self.status()

    def _capture_worker(
        self,
        device_index: int | None,
        chunk_size: int,
        save_wav: bool,
        wav_seconds: float,
    ) -> None:
        pyaudio = None
        audio = None
        stream = None
        writer: wave.Wave_write | None = None
        writer_path: Path | None = None
        writer_target_frames = 0
        writer_frames = 0
        started = False
        try:
            pyaudio = _load_pyaudio()
            audio = pyaudio.PyAudio()
            info = self._resolve_device(audio, pyaudio, device_index)
            channels = max(1, min(int(info.get("maxInputChannels") or 1), 2))
            sample_rate = int(float(info.get("defaultSampleRate") or 48000))
            selected_index = int(info["index"])
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                frames_per_buffer=chunk_size,
                input=True,
                input_device_index=selected_index,
            )

            if save_wav:
                self.capture_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                writer_path = self.capture_dir / f"chatgpt-audio-{stamp}.wav"
                writer = wave.open(str(writer_path), "wb")
                writer.setnchannels(channels)
                writer.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
                writer.setframerate(sample_rate)
                writer_target_frames = int(sample_rate * wav_seconds)

            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                self._started_monotonic = time.monotonic()
                self._state.update(
                    {
                        "active": True,
                        "device": {
                            "index": selected_index,
                            "name": str(info.get("name", selected_index)),
                            "is_loopback": bool(info.get("isLoopbackDevice", False)),
                        },
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "chunk_size": chunk_size,
                        "wav_path": str(writer_path) if writer_path else None,
                        "started_at": now,
                        "error": None,
                    }
                )
            started = True
            self._startup_event.set()

            while not self._stop_event.is_set():
                data = stream.read(chunk_size, exception_on_overflow=False)
                levels = calculate_pcm16_levels(data)
                frame_count = len(data) // (2 * channels)

                if writer is not None:
                    remaining = writer_target_frames - writer_frames
                    if remaining > 0:
                        bytes_per_frame = 2 * channels
                        writer.writeframes(data[: remaining * bytes_per_frame])
                        writer_frames += min(frame_count, remaining)
                    if writer_frames >= writer_target_frames:
                        writer.close()
                        writer = None
                        with self._lock:
                            self._state["wav_complete"] = True

                with self._lock:
                    self._state.update(levels)
                    self._state["frames_captured"] += frame_count
                    self._state["captured_seconds"] = round(
                        self._state["frames_captured"] / sample_rate, 3
                    )

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state["error"] = message
                self._state["active"] = False
            if not started:
                self._startup_error = message
                self._startup_event.set()
        finally:
            if writer is not None:
                writer.close()
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                stream.close()
            if audio is not None:
                audio.terminate()
            with self._lock:
                self._state["active"] = False
                self._state["rms"] = 0.0
                self._state["peak"] = 0.0
                self._state["dbfs"] = -96.0
                self._state["stopped_at"] = datetime.now(timezone.utc).isoformat()
            self._startup_event.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._state)
            result["thread_alive"] = bool(self._thread and self._thread.is_alive())
            if result["active"] and self._started_monotonic is not None:
                result["elapsed_seconds"] = round(time.monotonic() - self._started_monotonic, 3)
            else:
                result["elapsed_seconds"] = result.get("captured_seconds", 0.0)
            return result

    def stop(self, timeout: float = 6.0) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        result = self.status()
        if thread and thread.is_alive():
            result["error"] = "Audio capture thread did not stop within the timeout."
        return result

    def shutdown(self) -> None:
        self.stop()
