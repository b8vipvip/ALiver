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

from bridge.audio_capture import normalize_device_name

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
        try:
            number = float(source.get(key, result[key]))
        except (TypeError, ValueError):
            number = float(result[key])
        number = max(minimum, min(number, maximum))
        result[key] = int(round(number)) if key in {"sample_rate", "channels", "block_size"} else round(number, 3)
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
    raw_family = str(gpt_out.get("family") or "")
    if not raw_family:
        raw_family = str(
            dict(gpt_out.get("capture") or {}).get("virtual_family")
            or dict(gpt_out.get("playback") or {}).get("virtual_family")
            or ""
        )
    raw_pair = next((pair for pair in pairs if str(pair.get("family") or "") == raw_family), None)
    if raw_pair is None:
        raw_pair = next((pair for pair in pairs if str(pair.get("family") or "") == "vb-cable"), None)
    if raw_pair is None:
        raw_pair = next((pair for pair in pairs if pair.get("microphone") and pair.get("playback")), None)
    raw_microphone = dict((raw_pair or {}).get("microphone") or {})
    raw_playback = dict((raw_pair or {}).get("playback") or {})
    raw_family = str((raw_pair or {}).get("family") or raw_family)
    gpt_in_family = str(gpt_in.get("family") or "")

    candidates = [
        pair
        for pair in pairs
        if pair.get("playback")
        and pair.get("microphone")
        and str(pair.get("family") or "") not in {raw_family, gpt_in_family}
    ]
    candidates.sort(key=lambda item: _family_priority(str(item.get("family") or "")))
    processed_pair = candidates[0] if candidates else None
    output_playback = dict((processed_pair or {}).get("playback") or {})
    output_microphone = dict((processed_pair or {}).get("microphone") or {})
    output_loopback = dict((processed_pair or {}).get("loopback") or {})
    output_family = str((processed_pair or {}).get("family") or "")

    warnings: list[str] = []
    if not raw_microphone:
        warnings.append("未找到 ChatGPT 原声虚拟声卡的录音端（通常是 CABLE Output）。")
    if not processed_pair:
        warnings.append(
            "没有找到独立的处理后输出虚拟声卡。标准 VB-CABLE 用作原声输入、CABLE-A 用作 GPT_IN 时，"
            "还需要安装 CABLE-B（或选择另一组独立虚拟声卡）。"
        )
    return {
        "ready": bool(raw_microphone and output_playback and output_microphone),
        "input_microphone": raw_microphone or None,
        "input_playback": raw_playback or None,
        "input_family": raw_family or None,
        "output_playback": output_playback or None,
        "output_microphone": output_microphone or None,
        "output_loopback": output_loopback or None,
        "output_family": output_family or None,
        "gpt_in_family": gpt_in_family or None,
        "warnings": warnings,
        "instructions": {
            "chrome_output": raw_playback.get("name") or "CABLE Input (VB-Audio Virtual Cable)",
            "dsp_input": raw_microphone.get("name") if raw_microphone else None,
            "dsp_output": output_playback.get("name") if output_playback else None,
            "douyin_microphone": output_microphone.get("name") if output_microphone else None,
            "vtube_microphone": output_microphone.get("name") if output_microphone else None,
            "chatgpt_microphone": dict(gpt_in.get("microphone") or {}).get("name"),
        },
    }


def match_stream_device_name(candidates: list[str], requested: str) -> str | None:
    if not requested:
        return None
    if requested in candidates:
        return requested
    target = normalize_device_name(requested)
    normalized = [(name, normalize_device_name(name)) for name in candidates]
    exact = next((name for name, value in normalized if value == target), None)
    if exact:
        return exact
    contained = [name for name, value in normalized if target in value or value in target]
    return min(contained, key=len) if contained else None


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
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
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
        self._monitor_stop = threading.Event()
        self._startup = threading.Event()
        self._thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._stream: Any = None
        self._effect_board: Any = None
        self._config = self._load_config()
        self._recording: dict[str, Any] | None = None
        self._state: dict[str, Any] = {
            "status": "stopped",
            "running": False,
            "started_at": None,
            "stopped_at": None,
            "last_error": None,
            "monitor_error": None,
            "input_device": None,
            "output_device": None,
            "output_microphone": None,
            "output_loopback": None,
            "stream_input_name": None,
            "stream_output_name": None,
            "sample_rate": None,
            "channels": None,
            "block_size": None,
            "input_dbfs": -96.0,
            "input_peak_dbfs": -96.0,
            "output_dbfs": -96.0,
            "output_peak_dbfs": -96.0,
            "estimated_latency_ms": 0.0,
            "blocks_processed": 0,
            "xruns": 0,
            "recording": False,
            "last_recording": None,
        }

    def _load_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return normalize_dsp_config(DEFAULT_CONFIG)
        try:
            return normalize_dsp_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return normalize_dsp_config(DEFAULT_CONFIG)

    def _save_config(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8")

    def _scan(self) -> dict[str, Any]:
        scan = self.agent.audio.list_devices()
        scan["dsp_recommendation"] = recommend_dsp_routes(scan)
        return scan

    def devices(self) -> dict[str, Any]:
        scan = self._scan()
        stream_inputs: list[str] = []
        stream_outputs: list[str] = []
        if dependency_status()["pedalboard"]:
            try:
                from pedalboard.io import AudioStream

                stream_inputs = list(AudioStream.input_device_names)
                stream_outputs = list(AudioStream.output_device_names)
            except Exception:
                pass
        return {
            "dependencies": dependency_status(),
            "presets": DSP_PRESETS,
            "config": dict(self._config),
            "recommendation": scan["dsp_recommendation"],
            "input_devices": scan.get("input_devices") or [],
            "output_devices": scan.get("output_devices") or [],
            "loopback_devices": scan.get("loopback_devices") or [],
            "virtual_pairs": scan.get("virtual_pairs") or [],
            "pedalboard_input_device_names": stream_inputs,
            "pedalboard_output_device_names": stream_outputs,
            "status": self.status(),
        }

    def _resolve(self, scan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        recommendation = scan["dsp_recommendation"]
        input_key = str(self._config.get("input_device_key") or "")
        output_key = str(self._config.get("output_device_key") or "")
        inputs = [dict(row) for row in scan.get("input_devices") or []]
        outputs = [dict(row) for row in scan.get("output_devices") or []]
        loopbacks = [dict(row) for row in scan.get("loopback_devices") or []]
        input_device = next((row for row in inputs if row.get("key") == input_key), None)
        output_device = next((row for row in outputs if row.get("key") == output_key), None)
        input_device = input_device or dict(recommendation.get("input_microphone") or {}) or None
        output_device = output_device or dict(recommendation.get("output_playback") or {}) or None
        if not input_device or not output_device:
            raise RuntimeError("实时 DSP 需要原声虚拟声卡录音端和另一组独立的处理后输出虚拟声卡。")
        input_family = str(input_device.get("virtual_family") or "")
        output_family = str(output_device.get("virtual_family") or "")
        if input_family and output_family and input_family == output_family:
            raise RuntimeError("DSP 输入与输出不能使用同一组虚拟声卡，否则会形成音频回授。")
        output_microphone = next((row for row in inputs if row.get("virtual_family") == output_family), None)
        if output_microphone is None:
            raise RuntimeError("处理后输出虚拟声卡缺少录音端，直播伴侣无法接收 DSP 声音。")
        output_loopback = next((row for row in loopbacks if row.get("virtual_family") == output_family), None)
        return input_device, output_device, output_microphone, output_loopback

    def configure(self, values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        with self._lock:
            previous = dict(self._config)
            merged = {**self._config, **dict(values or {})}
            preset = str(merged.get("preset") or "custom")
            if preset in DSP_PRESETS and preset != previous.get("preset"):
                merged = apply_preset(merged, preset)
            else:
                merged = normalize_dsp_config(merged)
            restart_keys = {"input_device_key", "output_device_key", "block_size"}
            needs_restart = any(previous.get(key) != merged.get(key) for key in restart_keys)
            self._config = merged
            if persist:
                self._save_config()
            running = bool(self._thread and self._thread.is_alive() and self._state.get("running"))
            if running and not needs_restart:
                self._effect_board = self._make_board()
                if self._stream is not None:
                    self._stream.plugins = self._active_board()
        if running and needs_restart:
            self.stop(persist_disable=False)
            return self.start()
        return self.status()

    def _make_board(self):
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
                Limiter(threshold_db=float(config["limiter_threshold_db"]), release_ms=80.0),
            ]
        )
        return Pedalboard(plugins)

    def _active_board(self):
        if not bool(self._config.get("bypass")):
            return self._effect_board
        from pedalboard import Pedalboard

        return Pedalboard([])

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
        input_device, output_device, output_microphone, output_loopback = self._resolve(scan)
        with self._lock:
            self._config["input_device_key"] = str(input_device.get("key") or "")
            self._config["output_device_key"] = str(output_device.get("key") or "")
            self._config["enabled"] = True
            self._save_config()
            self._effect_board = self._make_board()
            self._stop.clear()
            self._startup.clear()
            self._state.update(
                {
                    "status": "starting",
                    "running": False,
                    "last_error": None,
                    "monitor_error": None,
                    "input_device": input_device,
                    "output_device": output_device,
                    "output_microphone": output_microphone,
                    "output_loopback": output_loopback,
                    "blocks_processed": 0,
                    "xruns": 0,
                }
            )
            self._thread = threading.Thread(
                target=self._run_stream,
                args=(input_device, output_device, output_microphone, output_loopback),
                name="aliver-realtime-voice-dsp",
                daemon=True,
            )
            self._thread.start()
        if not self._startup.wait(timeout=15.0):
            self.stop(persist_disable=False)
            raise RuntimeError("实时 DSP 音频流启动超时。")
        status = self.status()
        if status.get("status") == "failed":
            raise RuntimeError(str(status.get("last_error") or "实时 DSP 启动失败"))
        return status

    def _run_stream(
        self,
        input_device: dict[str, Any],
        output_device: dict[str, Any],
        output_microphone: dict[str, Any],
        output_loopback: dict[str, Any] | None,
    ) -> None:
        stream = None
        try:
            from pedalboard.io import AudioStream

            input_name = match_stream_device_name(list(AudioStream.input_device_names), str(input_device.get("name") or ""))
            output_name = match_stream_device_name(list(AudioStream.output_device_names), str(output_device.get("name") or ""))
            if not input_name:
                raise RuntimeError(
                    "Pedalboard 没有找到 DSP 输入设备："
                    f"{input_device.get('name')}。请确认该虚拟声卡录音端已启用。"
                )
            if not output_name:
                raise RuntimeError(
                    "Pedalboard 没有找到 DSP 输出设备："
                    f"{output_device.get('name')}。请确认该虚拟声卡播放端已启用。"
                )
            stream = AudioStream(input_name, output_name, buffer_size=int(self._config["block_size"]))
            stream.plugins = self._active_board()
            self._stream = stream
            with stream:
                rate = int(round(float(stream.sample_rate)))
                channels = max(1, min(int(stream.num_input_channels or 1), int(stream.num_output_channels or 1)))
                buffer_size = int(stream.buffer_size)
                with self._lock:
                    self._state.update(
                        {
                            "status": "running",
                            "running": True,
                            "started_at": utc_iso(),
                            "stream_input_name": input_name,
                            "stream_output_name": output_name,
                            "sample_rate": rate,
                            "channels": channels,
                            "block_size": buffer_size,
                            "estimated_latency_ms": round(buffer_size / rate * 1000.0, 2),
                        }
                    )
                self._apply_vtube_target(output_microphone)
                self._start_monitor(input_device, output_loopback, rate)
                self._startup.set()
                while not self._stop.wait(0.15):
                    with self._lock:
                        self._state["running"] = bool(stream.running)
                        dropped = int(getattr(stream, "dropped_input_frame_count", 0) or 0)
                        if dropped:
                            self._state["xruns"] = max(int(self._state.get("xruns") or 0), dropped)
                    if not stream.running:
                        raise RuntimeError("Pedalboard AudioStream 意外停止。")
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
            self._monitor_stop.set()
            monitor = self._monitor_thread
            if monitor and monitor.is_alive() and monitor is not threading.current_thread():
                monitor.join(timeout=3.0)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            self._stream = None
            with self._lock:
                if self._state.get("status") != "failed":
                    self._state["status"] = "stopped"
                self._state["running"] = False
                self._state["stopped_at"] = utc_iso()
                recording = self._recording
                self._recording = None
                self._state["recording"] = False
            if recording:
                recording["event"].set()
            self._startup.set()

    def _apply_vtube_target(self, output_microphone: dict[str, Any]) -> None:
        target = str(output_microphone.get("name") or "")
        manager = getattr(self.agent, "vtube_studio", None)
        if not target or manager is None:
            return
        for runtime in list(getattr(manager, "sessions", {}).values()):
            runtime.config["audio_device_name"] = target

    def _start_monitor(
        self,
        input_device: dict[str, Any],
        output_loopback: dict[str, Any] | None,
        sample_rate: int,
    ) -> None:
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(input_device, output_loopback, sample_rate),
            name="aliver-voice-dsp-meter",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(
        self,
        input_device: dict[str, Any],
        output_loopback: dict[str, Any] | None,
        sample_rate: int,
    ) -> None:
        audio = None
        streams: list[Any] = []
        try:
            import pyaudiowpatch as pyaudio

            audio = pyaudio.PyAudio()

            def callback(kind: str, channels: int):
                def on_audio(in_data, frame_count, time_info, status_flags):
                    del frame_count, time_info
                    samples = np.frombuffer(in_data, dtype=np.float32)
                    if samples.size and samples.size % channels == 0:
                        samples = samples.reshape((-1, channels)).T.copy()
                        with self._lock:
                            if kind == "original":
                                self._state["input_dbfs"] = _dbfs(samples)
                                self._state["input_peak_dbfs"] = _peak_dbfs(samples)
                            else:
                                self._state["output_dbfs"] = _dbfs(samples)
                                self._state["output_peak_dbfs"] = _peak_dbfs(samples)
                                self._state["blocks_processed"] = int(self._state.get("blocks_processed") or 0) + 1
                            if status_flags:
                                self._state["xruns"] = int(self._state.get("xruns") or 0) + 1
                            self._append_recording(kind, samples)
                    return (None, pyaudio.paContinue)

                return on_audio

            devices = [("original", input_device), ("processed", output_loopback)]
            for kind, device in devices:
                if not device:
                    continue
                info = audio.get_device_info_by_index(int(device["index"]))
                channels = max(1, min(2, int(info.get("maxInputChannels") or 1)))
                stream = audio.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    input_device_index=int(device["index"]),
                    frames_per_buffer=1024,
                    stream_callback=callback(kind, channels),
                    start=True,
                )
                streams.append(stream)
            if len(streams) < 2:
                with self._lock:
                    self._state["monitor_error"] = (
                        "实时变声正常，但没有找到处理后输出的 Loopback，输出电平和 A/B 双录不可用。"
                    )
            while not self._monitor_stop.wait(0.25):
                pass
        except Exception as exc:
            with self._lock:
                self._state["monitor_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            for stream in streams:
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

    def _append_recording(self, kind: str, samples: np.ndarray) -> None:
        recording = self._recording
        if not recording:
            return
        frames_key = f"{kind}_frames"
        arrays_key = kind
        remaining = int(recording["target_frames"]) - int(recording[frames_key])
        if remaining <= 0:
            return
        take = min(remaining, samples.shape[1])
        recording[arrays_key].append(samples[:, :take].copy())
        recording[frames_key] += take
        if (
            recording["original_frames"] >= recording["target_frames"]
            and recording["processed_frames"] >= recording["target_frames"]
        ):
            self._state["recording"] = False
            recording["event"].set()

    def set_bypass(self, bypass: bool) -> dict[str, Any]:
        with self._lock:
            self._config["bypass"] = bool(bypass)
            self._save_config()
            if self._stream is not None:
                self._stream.plugins = self._active_board()
        return self.status()

    def stop(self, *, persist_disable: bool = True) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=8.0)
        with self._lock:
            if persist_disable:
                self._config["enabled"] = False
                self._save_config()
            if not thread or not thread.is_alive():
                self._state["status"] = "stopped"
                self._state["running"] = False
        return self.status()

    @staticmethod
    def _write_wav(path: Path, data: np.ndarray, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm16 = (np.clip(data.T, -1.0, 1.0) * 32767.0).astype("<i2")
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
            if self._state.get("monitor_error"):
                raise RuntimeError(f"A/B 监测不可用：{self._state['monitor_error']}")
            if self._recording is not None:
                raise RuntimeError("已有一项 A/B 录制正在进行。")
            rate = int(self._state.get("sample_rate") or 48000)
            event = threading.Event()
            recording = {
                "event": event,
                "target_frames": int(rate * seconds),
                "original_frames": 0,
                "processed_frames": 0,
                "original": [],
                "processed": [],
            }
            self._recording = recording
            self._state["recording"] = True
        if not event.wait(timeout=seconds + 12.0):
            with self._lock:
                self._recording = None
                self._state["recording"] = False
            raise RuntimeError("A/B 录制等待音频超时；请确认 ChatGPT 正在朗读或说话。")
        with self._lock:
            if self._recording is recording:
                self._recording = None
            self._state["recording"] = False
        if not recording["original"] or not recording["processed"]:
            raise RuntimeError("A/B 录制没有同时捕获到原声和处理后音频。")
        original = np.concatenate(recording["original"], axis=1)
        processed = np.concatenate(recording["processed"], axis=1)
        frames = min(original.shape[1], processed.shape[1])
        original = original[:, :frames]
        processed = processed[:, :frames]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        original_path = CAPTURE_DIR / f"voice-dsp-ab-{stamp}-original.wav"
        processed_path = CAPTURE_DIR / f"voice-dsp-ab-{stamp}-processed.wav"
        self._write_wav(original_path, original, rate)
        self._write_wav(processed_path, processed, rate)
        result = {
            "seconds": round(frames / rate, 3),
            "sample_rate": rate,
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
                        "Chrome/ChatGPT 输出到原声虚拟声卡后，消息菜单中的朗读和 Voice 回答都会经过 DSP。"
                    ),
                    "streaming": (
                        "实际音频由 Pedalboard AudioStream 连续处理，避免逐块 PitchShift 造成静音或接缝爆音。"
                    ),
                    "formant": (
                        "第一版的年轻/成熟听感使用 PitchShift 与频谱塑形组合，不是独立神经网络 Formant 变换。"
                    ),
                },
            }

    def autostart(self) -> dict[str, Any]:
        if bool(self._config.get("enabled")):
            return self.start()
        return self.status()

    def shutdown(self) -> None:
        self.stop(persist_disable=False)
