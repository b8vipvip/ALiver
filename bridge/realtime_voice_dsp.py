from __future__ import annotations

import importlib.util
import json
import math
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from bridge.audio_capture import calculate_pcm16_levels

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "realtime_voice_dsp.local.json"
CAPTURE_DIR = BASE_DIR / "captures" / "voice_dsp"

DSP_PRESETS: dict[str, dict[str, float]] = {
    "original": {
        "pitch_semitones": 0.0,
        "tone_age": 0.0,
        "low_cut_hz": 55.0,
        "bass_db": 0.0,
        "presence_db": 0.0,
        "compressor_threshold_db": -18.0,
        "compressor_ratio": 1.4,
        "output_gain_db": 0.0,
    },
    "natural_girl": {
        "pitch_semitones": 1.5,
        "tone_age": 28.0,
        "low_cut_hz": 75.0,
        "bass_db": -1.5,
        "presence_db": 1.0,
        "compressor_threshold_db": -19.0,
        "compressor_ratio": 2.0,
        "output_gain_db": 0.0,
    },
    "sweet_young": {
        "pitch_semitones": 3.0,
        "tone_age": 58.0,
        "low_cut_hz": 90.0,
        "bass_db": -2.5,
        "presence_db": 1.8,
        "compressor_threshold_db": -20.0,
        "compressor_ratio": 2.4,
        "output_gain_db": -0.5,
    },
    "energetic": {
        "pitch_semitones": 2.3,
        "tone_age": 45.0,
        "low_cut_hz": 85.0,
        "bass_db": -2.0,
        "presence_db": 2.3,
        "compressor_threshold_db": -21.0,
        "compressor_ratio": 2.8,
        "output_gain_db": 0.0,
    },
    "gentle": {
        "pitch_semitones": 1.2,
        "tone_age": 18.0,
        "low_cut_hz": 65.0,
        "bass_db": -0.8,
        "presence_db": 0.5,
        "compressor_threshold_db": -18.0,
        "compressor_ratio": 1.8,
        "output_gain_db": -0.5,
    },
    "deep": {
        "pitch_semitones": -2.0,
        "tone_age": -45.0,
        "low_cut_hz": 50.0,
        "bass_db": 2.0,
        "presence_db": -1.0,
        "compressor_threshold_db": -18.0,
        "compressor_ratio": 2.2,
        "output_gain_db": -1.0,
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "bypass": False,
    "preset": "sweet_young",
    "input_device_key": "",
    "output_device_key": "",
    "sample_rate": 48000,
    "channels": 2,
    "block_size": 1024,
    "pitch_semitones": 3.0,
    "tone_age": 58.0,
    "low_cut_hz": 90.0,
    "bass_db": -2.5,
    "presence_db": 1.8,
    "compressor_threshold_db": -20.0,
    "compressor_ratio": 2.4,
    "compressor_attack_ms": 8.0,
    "compressor_release_ms": 110.0,
    "output_gain_db": -0.5,
    "limiter_threshold_db": -1.0,
}

LIMITS: dict[str, tuple[float, float]] = {
    "sample_rate": (16000.0, 96000.0),
    "channels": (1.0, 2.0),
    "block_size": (256.0, 4096.0),
    "pitch_semitones": (-8.0, 8.0),
    "tone_age": (-100.0, 100.0),
    "low_cut_hz": (20.0, 220.0),
    "bass_db": (-12.0, 12.0),
    "presence_db": (-12.0, 12.0),
    "compressor_threshold_db": (-48.0, 0.0),
    "compressor_ratio": (1.0, 10.0),
    "compressor_attack_ms": (0.1, 100.0),
    "compressor_release_ms": (10.0, 1000.0),
    "output_gain_db": (-18.0, 12.0),
    "limiter_threshold_db": (-12.0, 0.0),
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dependency_status() -> dict[str, Any]:
    values = {
        "numpy": importlib.util.find_spec("numpy") is not None,
        "pyaudiowpatch": importlib.util.find_spec("pyaudiowpatch") is not None,
        "pedalboard": importlib.util.find_spec("pedalboard") is not None,
    }
    return {**values, "ready": all(values.values())}


def normalize_dsp_config(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = dict(DEFAULT_CONFIG)
    for key in ("enabled", "bypass"):
        if key in source:
            result[key] = bool(source[key])
    for key in ("preset", "input_device_key", "output_device_key"):
        if key in source:
            result[key] = str(source[key] or "").strip()
    for key, (minimum, maximum) in LIMITS.items():
        raw = source.get(key, result[key])
        try:
            number = float(raw)
        except (TypeError, ValueError):
            number = float(result[key])
        number = max(minimum, min(number, maximum))
        if key in {"sample_rate", "channels", "block_size"}:
            result[key] = int(round(number))
        else:
            result[key] = round(number, 3)
    if result["block_size"] not in {256, 512, 1024, 2048, 4096}:
        result["block_size"] = 1024
    if result["preset"] not in DSP_PRESETS and result["preset"] != "custom":
        result["preset"] = "custom"
    return result


def apply_preset(config: dict[str, Any], preset: str) -> dict[str, Any]:
    values = dict(config)
    if preset in DSP_PRESETS:
        values.update(DSP_PRESETS[preset])
        values["preset"] = preset
    return normalize_dsp_config(values)


def _family_priority(family: str) -> tuple[int, str]:
    priorities = {
        "vb-cable-b": 0,
        "vb-cable-c": 1,
        "vb-cable-d": 2,
        "voicemeeter-aux": 3,
        "voicemeeter-vaio3": 4,
        "voicemeeter-main": 5,
        "vb-cable-a": 8,
        "vb-cable": 9,
    }
    return priorities.get(family, 6), family


def recommend_dsp_routes(scan: dict[str, Any]) -> dict[str, Any]:
    pairs = [dict(item) for item in scan.get("virtual_pairs") or [] if isinstance(item, dict)]
    routes = dict(scan.get("routes") or {})
    gpt_out = dict(routes.get("gpt_out") or {})
    gpt_in = dict(routes.get("gpt_in") or {})
    raw_capture = dict(gpt_out.get("capture") or {})
    raw_family = str(raw_capture.get("virtual_family") or gpt_out.get("family") or "")
    input_family = str(gpt_in.get("family") or "")

    if not raw_capture:
        preferred = sorted(
            [
                pair
                for pair in pairs
                if pair.get("loopback") and str(pair.get("family") or "") == "vb-cable"
            ],
            key=lambda item: _family_priority(str(item.get("family") or "")),
        )
        if not preferred:
            preferred = [pair for pair in pairs if pair.get("loopback")]
        if preferred:
            raw_capture = dict(preferred[0].get("loopback") or {})
            raw_family = str(preferred[0].get("family") or "")

    candidates = [
        pair
        for pair in pairs
        if pair.get("playback")
        and pair.get("microphone")
        and str(pair.get("family") or "") not in {raw_family, input_family}
    ]
    candidates.sort(key=lambda item: _family_priority(str(item.get("family") or "")))
    processed_pair = candidates[0] if candidates else None
    output_playback = dict((processed_pair or {}).get("playback") or {})
    output_microphone = dict((processed_pair or {}).get("microphone") or {})
    output_family = str((processed_pair or {}).get("family") or "")

    warnings: list[str] = []
    if not raw_capture:
        warnings.append("未找到 ChatGPT 原声对应的 WASAPI Loopback 设备。")
    if not processed_pair:
        warnings.append(
            "没有找到独立的处理后输出虚拟声卡。标准 VB-CABLE 用作原声输入、CABLE-A 用作 GPT_IN 时，"
            "还需要安装 CABLE-B（或选择另一组独立虚拟声卡）。"
        )
    return {
        "ready": bool(raw_capture and output_playback and output_microphone),
        "input_loopback": raw_capture or None,
        "input_family": raw_family or None,
        "output_playback": output_playback or None,
        "output_microphone": output_microphone or None,
        "output_family": output_family or None,
        "gpt_in_family": input_family or None,
        "warnings": warnings,
        "instructions": {
            "chrome_output": dict(gpt_out.get("playback") or {}).get("name")
            or "CABLE Input (VB-Audio Virtual Cable)",
            "dsp_input": raw_capture.get("name") if raw_capture else None,
            "dsp_output": output_playback.get("name") if output_playback else None,
            "douyin_microphone": output_microphone.get("name") if output_microphone else None,
            "vtube_microphone": output_microphone.get("name") if output_microphone else None,
            "chatgpt_microphone": dict(gpt_in.get("microphone") or {}).get("name"),
        },
    }


def _dbfs(array: np.ndarray) -> float:
    if array.size == 0:
        return -96.0
    rms = float(np.sqrt(np.mean(np.square(array.astype(np.float64)))))
    return round(max(-96.0, 20.0 * math.log10(max(rms, 1e-12))), 2)


def _peak_dbfs(array: np.ndarray) -> float:
    if array.size == 0:
        return -96.0
    peak = float(np.max(np.abs(array)))
    return round(max(-96.0, 20.0 * math.log10(max(peak, 1e-12))), 2)


def _spectral_centroid(array: np.ndarray, sample_rate: int) -> float:
    if array.size < 32:
        return 0.0
    mono = np.mean(array, axis=0) if array.ndim == 2 else array
    window = np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(mono * window))
    total = float(np.sum(spectrum))
    if total <= 1e-12:
        return 0.0
    frequencies = np.fft.rfftfreq(len(mono), d=1.0 / sample_rate)
    return round(float(np.sum(frequencies * spectrum) / total), 1)


class RealtimeVoiceDSPManager:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._startup = threading.Event()
        self._thread: threading.Thread | None = None
        self._board: Any = None
        self._config = self._load_config()
        self._state: dict[str, Any] = {
            "status": "stopped",
            "running": False,
            "started_at": None,
            "stopped_at": None,
            "last_error": None,
            "input_device": None,
            "output_device": None,
            "output_microphone": None,
            "sample_rate": None,
            "channels": None,
            "block_size": None,
            "input_dbfs": -96.0,
            "input_peak_dbfs": -96.0,
            "output_dbfs": -96.0,
            "output_peak_dbfs": -96.0,
            "process_ms": 0.0,
            "estimated_latency_ms": 0.0,
            "blocks_processed": 0,
            "xruns": 0,
            "recording": False,
            "last_recording": None,
        }
        self._recording: dict[str, Any] | None = None

    def _load_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return normalize_dsp_config(DEFAULT_CONFIG)
        try:
            return normalize_dsp_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return normalize_dsp_config(DEFAULT_CONFIG)

    def _save_config(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _scan(self) -> dict[str, Any]:
        scan = self.agent.audio.list_devices()
        scan["dsp_recommendation"] = recommend_dsp_routes(scan)
        return scan

    def devices(self) -> dict[str, Any]:
        scan = self._scan()
        return {
            "dependencies": dependency_status(),
            "presets": DSP_PRESETS,
            "config": dict(self._config),
            "recommendation": scan["dsp_recommendation"],
            "loopback_devices": scan.get("loopback_devices") or [],
            "output_devices": scan.get("output_devices") or [],
            "input_devices": scan.get("input_devices") or [],
            "virtual_pairs": scan.get("virtual_pairs") or [],
            "status": self.status(),
        }

    def _resolve(self, scan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        recommendation = scan["dsp_recommendation"]
        input_key = str(self._config.get("input_device_key") or "")
        output_key = str(self._config.get("output_device_key") or "")
        loopbacks = [dict(row) for row in scan.get("loopback_devices") or []]
        outputs = [dict(row) for row in scan.get("output_devices") or []]
        inputs = [dict(row) for row in scan.get("input_devices") or []]
        input_device = next((row for row in loopbacks if row.get("key") == input_key), None)
        output_device = next((row for row in outputs if row.get("key") == output_key), None)
        if input_device is None:
            input_device = dict(recommendation.get("input_loopback") or {}) or None
        if output_device is None:
            output_device = dict(recommendation.get("output_playback") or {}) or None
        if not input_device or not output_device:
            raise RuntimeError("实时 DSP 需要一组原声 Loopback 和另一组独立的处理后输出虚拟声卡。")
        input_family = str(input_device.get("virtual_family") or "")
        output_family = str(output_device.get("virtual_family") or "")
        if input_family and output_family and input_family == output_family:
            raise RuntimeError("DSP 输入与输出不能使用同一组虚拟声卡，否则会形成回授。")
        output_microphone = next(
            (row for row in inputs if row.get("virtual_family") == output_family),
            None,
        )
        return input_device, output_device, output_microphone

    def configure(self, values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        with self._lock:
            previous = dict(self._config)
            merged = {**self._config, **dict(values or {})}
            preset = str(merged.get("preset") or "custom")
            if preset in DSP_PRESETS and preset != previous.get("preset"):
                merged = apply_preset(merged, preset)
            else:
                merged = normalize_dsp_config(merged)
            restart_keys = {"input_device_key", "output_device_key", "sample_rate", "channels", "block_size"}
            needs_restart = any(previous.get(key) != merged.get(key) for key in restart_keys)
            self._config = merged
            if persist:
                self._save_config()
            running = bool(self._thread and self._thread.is_alive())
            if running and not needs_restart:
                self._board = self._make_board()
        if running and needs_restart:
            self.stop()
            return self.start()
        return self.status()

    def _make_board(self):
        try:
            from pedalboard import (
                Compressor,
                Gain,
                HighpassFilter,
                HighShelfFilter,
                Limiter,
                LowShelfFilter,
                Pedalboard,
                PitchShift,
            )
        except ImportError as exc:
            raise RuntimeError(
                "缺少 pedalboard 实时 DSP 依赖。请运行 "
                r".\.venv\Scripts\python.exe -m pip install -r requirements.txt"
            ) from exc
        config = dict(self._config)
        age = float(config["tone_age"])
        bass = float(config["bass_db"]) - max(age, 0.0) * 0.025 + max(-age, 0.0) * 0.018
        presence = float(config["presence_db"]) + max(age, 0.0) * 0.022 - max(-age, 0.0) * 0.012
        plugins = [HighpassFilter(cutoff_frequency_hz=float(config["low_cut_hz"]))]
        if abs(float(config["pitch_semitones"])) >= 0.01:
            plugins.append(PitchShift(semitones=float(config["pitch_semitones"])))
        if abs(bass) >= 0.01:
            plugins.append(LowShelfFilter(cutoff_frequency_hz=180.0, gain_db=bass, q=0.707))
        if abs(presence) >= 0.01:
            plugins.append(HighShelfFilter(cutoff_frequency_hz=4200.0, gain_db=presence, q=0.707))
        plugins.extend(
            [
                Compressor(
                    threshold_db=float(config["compressor_threshold_db"]),
                    ratio=float(config["compressor_ratio"]),
                    attack_ms=float(config["compressor_attack_ms"]),
                    release_ms=float(config["compressor_release_ms"]),
                ),
                Gain(gain_db=float(config["output_gain_db"])),
                Limiter(
                    threshold_db=float(config["limiter_threshold_db"]),
                    release_ms=80.0,
                ),
            ]
        )
        return Pedalboard(plugins)

    def start(self, values: dict[str, Any] | None = None) -> dict[str, Any]:
        if values:
            self.configure(values)
        deps = dependency_status()
        if not deps["ready"]:
            missing = ", ".join(key for key, ready in deps.items() if key != "ready" and not ready)
            raise RuntimeError(f"实时 DSP 依赖未安装：{missing}")
        if self._thread and self._thread.is_alive():
            return self.status()
        scan = self._scan()
        input_device, output_device, output_microphone = self._resolve(scan)
        with self._lock:
            self._config["input_device_key"] = str(input_device.get("key") or "")
            self._config["output_device_key"] = str(output_device.get("key") or "")
            self._config["enabled"] = True
            self._save_config()
            self._board = self._make_board()
            self._stop.clear()
            self._startup.clear()
            self._state.update(
                {
                    "status": "starting",
                    "running": False,
                    "last_error": None,
                    "input_device": input_device,
                    "output_device": output_device,
                    "output_microphone": output_microphone,
                    "blocks_processed": 0,
                    "xruns": 0,
                }
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(input_device, output_device),
                name="aliver-realtime-voice-dsp",
                daemon=True,
            )
            self._thread.start()
        if not self._startup.wait(timeout=12.0):
            self.stop()
            raise RuntimeError("实时 DSP 音频流启动超时。")
        status = self.status()
        if status.get("status") == "failed":
            raise RuntimeError(str(status.get("last_error") or "实时 DSP 启动失败"))
        return status

    def _run(self, input_device: dict[str, Any], output_device: dict[str, Any]) -> None:
        audio = None
        input_stream = None
        output_stream = None
        try:
            import pyaudiowpatch as pyaudio

            config = dict(self._config)
            audio = pyaudio.PyAudio()
            input_info = audio.get_device_info_by_index(int(input_device["index"]))
            output_info = audio.get_device_info_by_index(int(output_device["index"]))
            channels = min(
                int(config["channels"]),
                max(1, int(input_info.get("maxInputChannels") or 1)),
                max(1, int(output_info.get("maxOutputChannels") or 1)),
            )
            rate = int(config["sample_rate"])
            block_size = int(config["block_size"])
            input_stream = audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(input_device["index"]),
                frames_per_buffer=block_size,
                start=False,
            )
            output_stream = audio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                output=True,
                output_device_index=int(output_device["index"]),
                frames_per_buffer=block_size,
                start=False,
            )
            input_stream.start_stream()
            output_stream.start_stream()
            with self._lock:
                self._state.update(
                    {
                        "status": "running",
                        "running": True,
                        "started_at": utc_iso(),
                        "sample_rate": rate,
                        "channels": channels,
                        "block_size": block_size,
                        "estimated_latency_ms": round(block_size / rate * 1000.0, 2),
                    }
                )
            self._startup.set()
            while not self._stop.is_set():
                started = time.perf_counter()
                try:
                    raw = input_stream.read(block_size, exception_on_overflow=False)
                    original = np.frombuffer(raw, dtype=np.float32)
                    if original.size != block_size * channels:
                        with self._lock:
                            self._state["xruns"] = int(self._state.get("xruns") or 0) + 1
                        continue
                    original = original.reshape((-1, channels)).T.copy()
                    with self._lock:
                        bypass = bool(self._config.get("bypass"))
                        board = self._board
                    if bypass or board is None:
                        processed = original
                    else:
                        processed = np.asarray(board(original, rate, reset=False), dtype=np.float32)
                    if processed.ndim == 1:
                        processed = processed.reshape((1, -1))
                    if processed.shape[0] != channels and processed.shape[1] == channels:
                        processed = processed.T
                    if processed.shape[0] != channels:
                        processed = np.resize(processed, (channels, processed.shape[-1]))
                    if processed.shape[1] < block_size:
                        processed = np.pad(processed, ((0, 0), (0, block_size - processed.shape[1])))
                    elif processed.shape[1] > block_size:
                        processed = processed[:, :block_size]
                    processed = np.nan_to_num(processed, nan=0.0, posinf=1.0, neginf=-1.0)
                    processed = np.clip(processed, -1.0, 1.0)
                    output_stream.write(processed.T.astype(np.float32, copy=False).tobytes())
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    with self._lock:
                        previous = float(self._state.get("process_ms") or 0.0)
                        self._state.update(
                            {
                                "input_dbfs": _dbfs(original),
                                "input_peak_dbfs": _peak_dbfs(original),
                                "output_dbfs": _dbfs(processed),
                                "output_peak_dbfs": _peak_dbfs(processed),
                                "process_ms": round(previous * 0.85 + elapsed_ms * 0.15, 2),
                                "estimated_latency_ms": round(block_size / rate * 1000.0 + elapsed_ms, 2),
                                "blocks_processed": int(self._state.get("blocks_processed") or 0) + 1,
                            }
                        )
                        self._append_recording(original, processed, rate)
                except (OSError, ValueError) as exc:
                    with self._lock:
                        self._state["xruns"] = int(self._state.get("xruns") or 0) + 1
                        self._state["last_error"] = f"{type(exc).__name__}: {exc}"
                    time.sleep(0.01)
        except Exception as exc:
            with self._lock:
                self._state.update(
                    {
                        "status": "failed",
                        "running": False,
                        "last_error": f"{type(exc).__name__}: {exc}",
                    }
                )
            self._startup.set()
        finally:
            for stream in (input_stream, output_stream):
                if stream is None:
                    continue
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                try:
                    audio.terminate()
                except Exception:
                    pass
            with self._lock:
                if self._state.get("status") != "failed":
                    self._state["status"] = "stopped"
                self._state["running"] = False
                self._state["stopped_at"] = utc_iso()
                self._state["recording"] = False
                recording = self._recording
                self._recording = None
            if recording:
                recording["event"].set()
            self._startup.set()

    def _append_recording(self, original: np.ndarray, processed: np.ndarray, rate: int) -> None:
        recording = self._recording
        if not recording:
            return
        remaining = int(recording["target_frames"]) - int(recording["frames"])
        if remaining <= 0:
            return
        take = min(remaining, original.shape[1], processed.shape[1])
        recording["original"].append(original[:, :take].copy())
        recording["processed"].append(processed[:, :take].copy())
        recording["frames"] += take
        if recording["frames"] >= recording["target_frames"]:
            self._state["recording"] = False
            recording["event"].set()

    def set_bypass(self, bypass: bool) -> dict[str, Any]:
        with self._lock:
            self._config["bypass"] = bool(bypass)
            self._save_config()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=8.0)
        with self._lock:
            self._config["enabled"] = False
            self._save_config()
            if not thread or not thread.is_alive():
                self._state["status"] = "stopped"
                self._state["running"] = False
        return self.status()

    @staticmethod
    def _write_wav(path: Path, data: np.ndarray, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = np.clip(data.T, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(data.shape[0])
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm16.tobytes())

    def record_compare(self, seconds: float = 10.0) -> dict[str, Any]:
        seconds = max(2.0, min(float(seconds), 30.0))
        with self._lock:
            if not self._state.get("running"):
                raise RuntimeError("请先启动实时 DSP，再录制 A/B 对比。")
            if self._recording is not None:
                raise RuntimeError("已有一项 A/B 录制正在进行。")
            rate = int(self._state.get("sample_rate") or self._config["sample_rate"])
            event = threading.Event()
            recording = {
                "event": event,
                "target_frames": int(rate * seconds),
                "frames": 0,
                "original": [],
                "processed": [],
            }
            self._recording = recording
            self._state["recording"] = True
        if not event.wait(timeout=seconds + 10.0):
            with self._lock:
                self._recording = None
                self._state["recording"] = False
            raise RuntimeError("A/B 录制等待音频超时；请确认 ChatGPT 正在朗读或说话。")
        with self._lock:
            if self._recording is recording:
                self._recording = None
            self._state["recording"] = False
        if not recording["original"] or not recording["processed"]:
            raise RuntimeError("A/B 录制没有捕获到音频数据。")
        original = np.concatenate(recording["original"], axis=1)
        processed = np.concatenate(recording["processed"], axis=1)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        original_path = CAPTURE_DIR / f"voice-dsp-ab-{stamp}-original.wav"
        processed_path = CAPTURE_DIR / f"voice-dsp-ab-{stamp}-processed.wav"
        self._write_wav(original_path, original, rate)
        self._write_wav(processed_path, processed, rate)
        result = {
            "seconds": round(original.shape[1] / rate, 3),
            "sample_rate": rate,
            "channels": original.shape[0],
            "original_path": str(original_path),
            "processed_path": str(processed_path),
            "original": {
                "dbfs": _dbfs(original),
                "peak_dbfs": _peak_dbfs(original),
                "spectral_centroid_hz": _spectral_centroid(original, rate),
            },
            "processed": {
                "dbfs": _dbfs(processed),
                "peak_dbfs": _peak_dbfs(processed),
                "spectral_centroid_hz": _spectral_centroid(processed, rate),
            },
            "created_at": utc_iso(),
        }
        with self._lock:
            self._state["last_recording"] = result
        return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **dict(self._state),
                "running": bool(self._thread and self._thread.is_alive() and self._state.get("running")),
                "dependencies": dependency_status(),
                "config": dict(self._config),
                "presets": DSP_PRESETS,
                "notes": {
                    "read_aloud": (
                        "只要 Chrome/ChatGPT 输出到 DSP 输入虚拟声卡，消息菜单中的“朗读”和 Voice 回答都会经过 DSP。"
                    ),
                    "formant": (
                        "第一版的年龄感使用 PitchShift 与频谱塑形组合，不是独立的神经网络 Formant 变换。"
                    ),
                },
            }

    def shutdown(self) -> None:
        self.stop()
