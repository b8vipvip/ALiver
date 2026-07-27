from __future__ import annotations

import hashlib
import json
import math
import os
import re
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
ROUTES_FILE = BASE_DIR / "audio_routes.json"

SILENCE_DBFS = -78.0
VIRTUAL_HINTS = (
    "vb-audio",
    "virtual cable",
    "cable input",
    "cable output",
    "cable-a input",
    "cable-a output",
    "cable-b input",
    "cable-b output",
    "voicemeeter",
    "todesk virtual audio",
)


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


def normalize_device_name(name: str) -> str:
    value = name.lower().replace("[loopback]", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def virtual_family(name: str) -> str | None:
    """Return a stable logical virtual-cable family for common Windows devices."""
    value = normalize_device_name(name)
    if "cable-a input" in value or "cable-a output" in value:
        return "vb-cable-a"
    if "cable-b input" in value or "cable-b output" in value:
        return "vb-cable-b"
    if "cable input" in value or "cable output" in value:
        return "vb-cable"
    if "voicemeeter aux" in value:
        return "voicemeeter-aux"
    if "voicemeeter vaio3" in value:
        return "voicemeeter-vaio3"
    if "voicemeeter" in value:
        return "voicemeeter-main"
    if "todesk virtual audio" in value:
        return "todesk-virtual-audio"
    if any(hint in value for hint in VIRTUAL_HINTS):
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        return f"virtual-{digest}"
    return None


def device_key(name: str, kind: str, is_loopback: bool) -> str:
    raw = f"{normalize_device_name(name)}|{kind}|{int(is_loopback)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_pyaudio():
    if os.name != "nt":
        raise RuntimeError("WASAPI audio routing is available only on Windows.")
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise RuntimeError(
            "PyAudioWPatch is not installed. Run "
            r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
        ) from exc
    return pyaudio


class AudioCaptureManager:
    """Owns one GPT_OUT WASAPI capture stream and GPT_IN test playback."""

    def __init__(
        self,
        capture_dir: Path | None = None,
        routes_file: Path | None = None,
    ) -> None:
        self.capture_dir = capture_dir or CAPTURE_DIR
        self.routes_file = routes_file or ROUTES_FILE
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._startup_event = threading.Event()
        self._startup_error: str | None = None
        self._started_monotonic: float | None = None
        self._state: dict[str, Any] = self._empty_state()
        self._routes = self._load_routes()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "role": "gpt_out",
            "active": False,
            "device": None,
            "sample_rate": None,
            "channels": None,
            "chunk_size": None,
            "rms": 0.0,
            "peak": 0.0,
            "dbfs": -96.0,
            "max_dbfs": -96.0,
            "non_silent_chunks": 0,
            "captured_seconds": 0.0,
            "frames_captured": 0,
            "wav_path": None,
            "wav_complete": False,
            "auto_stop": True,
            "target_seconds": None,
            "started_at": None,
            "stopped_at": None,
            "error": None,
            "diagnosis": {
                "code": "not_started",
                "message": "尚未进行 GPT_OUT 捕获测试。",
            },
        }

    @staticmethod
    def _default_routes() -> dict[str, Any]:
        return {
            "version": 1,
            "gpt_out": {
                "capture_device_key": None,
                "capture_device_name": None,
                "playback_device_key": None,
                "playback_device_name": None,
                "family": None,
            },
            "gpt_in": {
                "playback_device_key": None,
                "playback_device_name": None,
                "microphone_device_key": None,
                "microphone_device_name": None,
                "family": None,
            },
            "updated_at": None,
        }

    def _load_routes(self) -> dict[str, Any]:
        if not self.routes_file.exists():
            return self._default_routes()
        try:
            value = json.loads(self.routes_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._default_routes()
        routes = self._default_routes()
        routes.update({key: value.get(key, routes[key]) for key in routes})
        return routes

    def _save_routes_file(self) -> None:
        self.routes_file.write_text(
            json.dumps(self._routes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
        name = str(info.get("name", f"Device {index}"))
        family = virtual_family(name)
        return {
            "index": index,
            "key": device_key(name, kind, is_loopback),
            "name": name,
            "normalized_name": normalize_device_name(name),
            "kind": kind,
            "is_loopback": is_loopback,
            "is_virtual": family is not None,
            "virtual_family": family,
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

            loopbacks = [row for row in devices if row["is_loopback"]]
            inputs = [
                row
                for row in devices
                if row["kind"] == "input" and not row["is_loopback"]
            ]
            outputs = [
                row
                for row in devices
                if row["kind"] == "output" and row["output_channels"] > 0
            ]

            pairs = self._build_virtual_pairs(loopbacks, inputs, outputs)
            recommendations = self._recommend_routes(pairs)

            loopbacks.sort(
                key=lambda row: (
                    not row["is_virtual"],
                    not row["is_default_loopback"],
                    row["name"].lower(),
                )
            )
            outputs.sort(
                key=lambda row: (
                    not row["is_virtual"],
                    not row["is_default_output"],
                    row["name"].lower(),
                )
            )
            inputs.sort(
                key=lambda row: (
                    not row["is_virtual"],
                    not row["is_default_input"],
                    row["name"].lower(),
                )
            )
            return {
                "backend": "PyAudioWPatch/WASAPI",
                "loopback_devices": loopbacks,
                "input_devices": inputs,
                "output_devices": outputs,
                "virtual_pairs": pairs,
                "recommendations": recommendations,
                "routes": self._route_status_from_devices(loopbacks, inputs, outputs, pairs),
                "defaults": defaults,
            }
        finally:
            audio.terminate()

    @staticmethod
    def _build_virtual_pairs(
        loopbacks: list[dict[str, Any]],
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        families = sorted(
            {
                row["virtual_family"]
                for row in [*loopbacks, *inputs, *outputs]
                if row.get("virtual_family")
            }
        )
        pairs: list[dict[str, Any]] = []
        for family in families:
            family_loopbacks = [row for row in loopbacks if row.get("virtual_family") == family]
            family_inputs = [row for row in inputs if row.get("virtual_family") == family]
            family_outputs = [row for row in outputs if row.get("virtual_family") == family]
            pairs.append(
                {
                    "family": family,
                    "loopback": family_loopbacks[0] if family_loopbacks else None,
                    "microphone": family_inputs[0] if family_inputs else None,
                    "playback": family_outputs[0] if family_outputs else None,
                    "complete": bool(family_loopbacks and family_inputs and family_outputs),
                }
            )
        return pairs

    @staticmethod
    def _recommend_routes(pairs: list[dict[str, Any]]) -> dict[str, Any]:
        def priority(pair: dict[str, Any]) -> tuple[int, str]:
            family = str(pair.get("family") or "")
            if family == "vb-cable":
                return (0, family)
            if family in {"vb-cable-a", "vb-cable-b"}:
                return (1, family)
            if family.startswith("voicemeeter"):
                return (2, family)
            if family == "todesk-virtual-audio":
                return (9, family)
            return (5, family)

        usable_out = sorted(
            [pair for pair in pairs if pair.get("loopback") and pair.get("playback")],
            key=priority,
        )
        usable_in = sorted(
            [pair for pair in pairs if pair.get("playback") and pair.get("microphone")],
            key=priority,
        )
        gpt_out = usable_out[0] if usable_out else None
        gpt_in = next(
            (
                pair
                for pair in usable_in
                if not gpt_out or pair.get("family") != gpt_out.get("family")
            ),
            usable_in[0] if usable_in else None,
        )
        isolated = bool(
            gpt_out
            and gpt_in
            and gpt_out.get("family") != gpt_in.get("family")
        )
        warnings: list[str] = []
        if not gpt_out:
            warnings.append("未发现可作为 GPT_OUT 的虚拟扬声器回放设备。")
        if not gpt_in:
            warnings.append("未发现可作为 GPT_IN 的虚拟扬声器/麦克风配对。")
        if gpt_out and gpt_in and not isolated:
            warnings.append("只发现一组虚拟声卡，GPT_IN 与 GPT_OUT 尚未隔离。请安装第二组 VB-CABLE A/B。")
        return {
            "gpt_out": gpt_out,
            "gpt_in": gpt_in,
            "isolated": isolated,
            "ready": bool(gpt_out and gpt_in and isolated),
            "warnings": warnings,
        }

    @staticmethod
    def _find_by_key(devices: list[dict[str, Any]], key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        return next((row for row in devices if row.get("key") == key), None)

    def _route_status_from_devices(
        self,
        loopbacks: list[dict[str, Any]],
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        pairs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        gpt_out = self._routes.get("gpt_out") or {}
        gpt_in = self._routes.get("gpt_in") or {}
        out_capture = self._find_by_key(loopbacks, gpt_out.get("capture_device_key"))
        out_playback = self._find_by_key(outputs, gpt_out.get("playback_device_key"))
        in_playback = self._find_by_key(outputs, gpt_in.get("playback_device_key"))
        in_microphone = self._find_by_key(inputs, gpt_in.get("microphone_device_key"))
        out_family = (out_capture or out_playback or {}).get("virtual_family")
        in_family = (in_playback or in_microphone or {}).get("virtual_family")
        warnings: list[str] = []
        if not out_capture:
            warnings.append("GPT_OUT 捕获设备尚未配置或设备已变化。")
        if not in_playback:
            warnings.append("GPT_IN 播放设备尚未配置或设备已变化。")
        if not in_microphone:
            warnings.append("未找到与 GPT_IN 播放设备配对的虚拟麦克风。")
        if out_family and in_family and out_family == in_family:
            warnings.append("GPT_IN 与 GPT_OUT 使用同一虚拟声卡，存在回灌风险。")
        return {
            "configured": self._routes,
            "gpt_out": {
                "capture": out_capture,
                "playback": out_playback,
                "ready": bool(out_capture),
            },
            "gpt_in": {
                "playback": in_playback,
                "microphone": in_microphone,
                "ready": bool(in_playback and in_microphone),
            },
            "isolated": bool(out_family and in_family and out_family != in_family),
            "ready": bool(
                out_capture
                and in_playback
                and in_microphone
                and out_family
                and in_family
                and out_family != in_family
            ),
            "warnings": warnings,
            "pairs": pairs,
        }

    def get_routes(self) -> dict[str, Any]:
        return self.list_devices()["routes"]

    def save_routes(
        self,
        *,
        gpt_out_capture_key: str,
        gpt_in_playback_key: str,
    ) -> dict[str, Any]:
        scanned = self.list_devices()
        loopbacks = scanned["loopback_devices"]
        inputs = scanned["input_devices"]
        outputs = scanned["output_devices"]
        out_capture = self._find_by_key(loopbacks, gpt_out_capture_key)
        in_playback = self._find_by_key(outputs, gpt_in_playback_key)
        if not out_capture:
            raise ValueError("Selected GPT_OUT capture device was not found.")
        if not in_playback:
            raise ValueError("Selected GPT_IN playback device was not found.")
        if not out_capture.get("is_virtual"):
            raise ValueError("GPT_OUT must use a virtual loopback device, not a physical speaker.")
        if not in_playback.get("is_virtual"):
            raise ValueError("GPT_IN must use a virtual playback device, not a physical speaker.")
        out_family = out_capture.get("virtual_family")
        in_family = in_playback.get("virtual_family")
        if out_family == in_family:
            raise ValueError(
                "GPT_IN and GPT_OUT cannot use the same virtual cable. Install/select a second cable."
            )

        out_pair = next(
            (pair for pair in scanned["virtual_pairs"] if pair.get("family") == out_family),
            {},
        )
        in_pair = next(
            (pair for pair in scanned["virtual_pairs"] if pair.get("family") == in_family),
            {},
        )
        microphone = in_pair.get("microphone")
        if not microphone:
            raise ValueError("The selected GPT_IN cable has no matching virtual microphone endpoint.")

        self._routes = {
            "version": 1,
            "gpt_out": {
                "capture_device_key": out_capture["key"],
                "capture_device_name": out_capture["name"],
                "playback_device_key": (out_pair.get("playback") or {}).get("key"),
                "playback_device_name": (out_pair.get("playback") or {}).get("name"),
                "family": out_family,
            },
            "gpt_in": {
                "playback_device_key": in_playback["key"],
                "playback_device_name": in_playback["name"],
                "microphone_device_key": microphone["key"],
                "microphone_device_name": microphone["name"],
                "family": in_family,
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_routes_file()
        return self.get_routes()

    def apply_recommendations(self) -> dict[str, Any]:
        scanned = self.list_devices()
        recommendations = scanned["recommendations"]
        gpt_out = recommendations.get("gpt_out") or {}
        gpt_in = recommendations.get("gpt_in") or {}
        if not recommendations.get("ready"):
            warnings = "; ".join(recommendations.get("warnings") or [])
            raise ValueError(warnings or "Two isolated virtual audio cables were not found.")
        return self.save_routes(
            gpt_out_capture_key=gpt_out["loopback"]["key"],
            gpt_in_playback_key=gpt_in["playback"]["key"],
        )

    def _resolve_key(self, role: str) -> int:
        scanned = self.list_devices()
        if role == "gpt_out":
            key = (self._routes.get("gpt_out") or {}).get("capture_device_key")
            row = self._find_by_key(scanned["loopback_devices"], key)
        elif role == "gpt_in":
            key = (self._routes.get("gpt_in") or {}).get("playback_device_key")
            row = self._find_by_key(scanned["output_devices"], key)
        else:
            raise ValueError(f"Unknown audio route role: {role}")
        if not row:
            raise RuntimeError(f"{role.upper()} route is not configured or the device is unavailable.")
        return int(row["index"])

    def start_gpt_out(
        self,
        *,
        duration_seconds: float = 10.0,
        save_wav: bool = True,
        auto_stop: bool = True,
        chunk_size: int = 1024,
    ) -> dict[str, Any]:
        device_index = self._resolve_key("gpt_out")
        return self.start(
            device_index,
            chunk_size=chunk_size,
            save_wav=save_wav,
            wav_seconds=duration_seconds,
            auto_stop=auto_stop,
        )

    def play_gpt_in_test_tone(
        self,
        *,
        duration_seconds: float = 2.0,
        frequency_hz: float = 660.0,
        volume: float = 0.18,
    ) -> dict[str, Any]:
        device_index = self._resolve_key("gpt_in")
        duration_seconds = max(0.2, min(float(duration_seconds), 10.0))
        frequency_hz = max(100.0, min(float(frequency_hz), 2000.0))
        volume = max(0.01, min(float(volume), 0.5))

        pyaudio = _load_pyaudio()
        audio = pyaudio.PyAudio()
        stream = None
        try:
            info = dict(audio.get_device_info_by_index(device_index))
            channels = max(1, min(int(info.get("maxOutputChannels") or 2), 2))
            sample_rate = int(float(info.get("defaultSampleRate") or 48000))
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=1024,
            )
            total_frames = int(sample_rate * duration_seconds)
            chunk = 1024
            amplitude = int(32767 * volume)
            frame_cursor = 0
            while frame_cursor < total_frames:
                count = min(chunk, total_frames - frame_cursor)
                values = array("h")
                for offset in range(count):
                    sample = int(
                        amplitude
                        * math.sin(2.0 * math.pi * frequency_hz * (frame_cursor + offset) / sample_rate)
                    )
                    for _ in range(channels):
                        values.append(sample)
                if sys.byteorder != "little":
                    values.byteswap()
                stream.write(values.tobytes())
                frame_cursor += count
            return {
                "played": True,
                "role": "gpt_in",
                "device": {
                    "index": device_index,
                    "name": str(info.get("name", device_index)),
                },
                "duration_seconds": duration_seconds,
                "frequency_hz": frequency_hz,
                "microphone_hint": (self._routes.get("gpt_in") or {}).get(
                    "microphone_device_name"
                ),
                "message": (
                    "Test tone was sent into GPT_IN. Select the matching microphone in ChatGPT Live."
                ),
            }
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                stream.close()
            audio.terminate()

    def _resolve_device(self, audio, device_index: int | None) -> dict[str, Any]:
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
        auto_stop: bool = False,
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
            self._state["auto_stop"] = bool(auto_stop)
            self._state["target_seconds"] = wav_seconds
            self._thread = threading.Thread(
                target=self._capture_worker,
                args=(device_index, chunk_size, save_wav, wav_seconds, auto_stop),
                name="aliver-gpt-out-capture",
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
        auto_stop: bool,
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
            info = self._resolve_device(audio, device_index)
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
                writer_path = self.capture_dir / f"gpt-out-{stamp}.wav"
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
                            "key": device_key(
                                str(info.get("name", selected_index)),
                                "loopback",
                                bool(info.get("isLoopbackDevice", False)),
                            ),
                            "name": str(info.get("name", selected_index)),
                            "is_loopback": bool(info.get("isLoopbackDevice", False)),
                        },
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "chunk_size": chunk_size,
                        "wav_path": str(writer_path) if writer_path else None,
                        "started_at": now,
                        "diagnosis": {
                            "code": "waiting_for_signal",
                            "message": "正在等待 ChatGPT 输出语音。",
                        },
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
                    self._state["max_dbfs"] = max(
                        float(self._state.get("max_dbfs", -96.0)),
                        float(levels["dbfs"]),
                    )
                    if levels["dbfs"] > SILENCE_DBFS:
                        self._state["non_silent_chunks"] += 1
                    self._state["frames_captured"] += frame_count
                    self._state["captured_seconds"] = round(
                        self._state["frames_captured"] / sample_rate,
                        3,
                    )
                    elapsed = self._state["captured_seconds"]
                    if self._state["non_silent_chunks"] > 0:
                        self._state["diagnosis"] = {
                            "code": "signal_ok",
                            "message": "已检测到 ChatGPT 输出信号。",
                        }
                    elif elapsed >= 3.0:
                        self._state["diagnosis"] = {
                            "code": "silent_wrong_route",
                            "message": (
                                "连续静音：ChatGPT 可能没有输出到已配置的 GPT_OUT 虚拟扬声器。"
                            ),
                        }

                if auto_stop and self.status()["captured_seconds"] >= wav_seconds:
                    break

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._state["error"] = message
                self._state["active"] = False
                self._state["diagnosis"] = {"code": "error", "message": message}
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
                if self._state.get("non_silent_chunks", 0) == 0 and not self._state.get("error"):
                    self._state["diagnosis"] = {
                        "code": "silent_wrong_route",
                        "message": (
                            "测试全程未检测到声音。请把 Chrome 输出切换到 GPT_OUT 对应的虚拟扬声器。"
                        ),
                    }
                elif not self._state.get("error"):
                    self._state["diagnosis"] = {
                        "code": "completed_signal_ok",
                        "message": "GPT_OUT 捕获测试完成并检测到语音信号。",
                    }
            self._startup_event.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._state)
            result["diagnosis"] = dict(self._state.get("diagnosis") or {})
            result["thread_alive"] = bool(self._thread and self._thread.is_alive())
            if result["active"] and self._started_monotonic is not None:
                result["elapsed_seconds"] = round(
                    time.monotonic() - self._started_monotonic,
                    3,
                )
            else:
                result["elapsed_seconds"] = result.get("captured_seconds", 0.0)
            return result

    def stop(self, timeout: float = 6.0) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        result = self.status()
        if result["thread_alive"]:
            result["error"] = "Audio capture thread did not stop within the timeout."
        return result

    def shutdown(self) -> None:
        self.stop()
