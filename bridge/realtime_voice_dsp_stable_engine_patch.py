from __future__ import annotations

import copy
from typing import Any

import numpy as np

from bridge import realtime_voice_dsp as dsp


def _is_running(manager: Any) -> bool:
    return bool(
        manager._thread
        and manager._thread.is_alive()
        and manager._state.get("running")
    )


def _candidate_rates(config: dict[str, Any], input_info: dict, output_info: dict) -> list[int]:
    values = [
        config.get("sample_rate"),
        input_info.get("defaultSampleRate"),
        output_info.get("defaultSampleRate"),
        48000,
        44100,
    ]
    result: list[int] = []
    for value in values:
        try:
            rate = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if rate > 0 and rate not in result:
            result.append(rate)
    return result


def _normalise_processed(samples: np.ndarray, channels: int, frames: int) -> np.ndarray:
    value = np.asarray(samples, dtype=np.float32)
    if value.ndim == 1:
        value = value[np.newaxis, :]
    if value.shape[0] != channels and value.shape[1] == channels:
        value = value.T
    if value.shape[0] > channels:
        value = value[:channels]
    elif value.shape[0] == 1 and channels == 2:
        value = np.repeat(value, 2, axis=0)
    elif value.shape[0] < channels:
        value = np.pad(value, ((0, channels - value.shape[0]), (0, 0)))
    if value.shape[1] > frames:
        value = value[:, :frames]
    elif value.shape[1] < frames:
        value = np.pad(value, ((0, 0), (0, frames - value.shape[1])))
    return np.ascontiguousarray(value, dtype=np.float32)


def install_realtime_voice_dsp_stable_engine_patch() -> None:
    """Keep realtime DSP, meters and A/B recording on one PortAudio owner.

    The previous implementation ran Pedalboard AudioStream for DSP and created a
    second PyAudioWPatch instance for meters/A/B capture. On Windows, opening or
    terminating that second PortAudio owner while the DSP stream was active
    could terminate python.exe inside _portaudiowpatch. The monitor also tried
    the Pedalboard stream rate (often 44.1 kHz) on 48 kHz virtual endpoints.

    This patch uses one PyAudioWPatch full-duplex stream. The same audio block is
    measured, recorded, processed by Pedalboard and written to CABLE-B, so no
    second monitor stream or active-stream device enumeration is required.
    """

    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_stable_single_portaudio_engine", False):
        return

    original_scan = manager_class._scan

    def scan(self: Any) -> dict[str, Any]:
        cached = getattr(self, "_aliver_dsp_scan_cache", None)
        if _is_running(self) and isinstance(cached, dict):
            return copy.deepcopy(cached)
        result = original_scan(self)
        self._aliver_dsp_scan_cache = copy.deepcopy(result)
        return copy.deepcopy(result)

    def devices(self: Any) -> dict[str, Any]:
        scan_result = self._scan()
        return {
            "dependencies": dsp.dependency_status(),
            "presets": dsp.DSP_PRESETS,
            "config": dict(self._config),
            "recommendation": scan_result["dsp_recommendation"],
            "input_devices": scan_result.get("input_devices") or [],
            "output_devices": scan_result.get("output_devices") or [],
            "loopback_devices": scan_result.get("loopback_devices") or [],
            "virtual_pairs": scan_result.get("virtual_pairs") or [],
            # Do not ask another PortAudio binding to enumerate devices while a
            # realtime stream is active. These fields are diagnostic only.
            "pedalboard_input_device_names": [],
            "pedalboard_output_device_names": [],
            "status": self.status(),
        }

    def configure(self: Any, values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        with self._lock:
            previous = dict(self._config)
            merged = {**self._config, **dict(values or {})}
            preset = str(merged.get("preset") or "custom")
            if preset in dsp.DSP_PRESETS and preset != previous.get("preset"):
                merged = dsp.apply_preset(merged, preset)
            else:
                merged = dsp.normalize_dsp_config(merged)
            restart_keys = {"input_device_key", "output_device_key", "block_size"}
            needs_restart = any(previous.get(key) != merged.get(key) for key in restart_keys)
            self._config = merged
            if persist:
                self._save_config()
            running = _is_running(self)
            if running and not needs_restart:
                # The processing loop reads the current board once per block.
                # Never assign a .plugins attribute to the PyAudio stream.
                self._effect_board = self._make_board()
        if running and needs_restart:
            self.stop(persist_disable=False)
            return self.start()
        return self.status()

    def set_bypass(self: Any, bypass: bool) -> dict[str, Any]:
        with self._lock:
            self._config["bypass"] = bool(bypass)
            self._save_config()
        return self.status()

    def run_stream(
        self: Any,
        input_device: dict[str, Any],
        output_device: dict[str, Any],
        output_microphone: dict[str, Any],
        output_loopback: dict[str, Any] | None,
    ) -> None:
        del output_loopback
        audio = None
        stream = None
        recording = None
        try:
            import pyaudiowpatch as pyaudio

            audio = pyaudio.PyAudio()
            input_index = int(input_device["index"])
            output_index = int(output_device["index"])
            input_info = audio.get_device_info_by_index(input_index)
            output_info = audio.get_device_info_by_index(output_index)
            max_channels = min(
                int(input_info.get("maxInputChannels") or 0),
                int(output_info.get("maxOutputChannels") or 0),
                int(self._config.get("channels") or 2),
            )
            if max_channels < 1:
                raise RuntimeError("DSP 输入或输出设备没有可用的音频通道。")

            block_size = int(self._config.get("block_size") or 1024)
            attempts: list[str] = []
            selected_rate = 0
            selected_channels = 0
            for channels in dict.fromkeys((max_channels, 1)):
                for rate in _candidate_rates(self._config, input_info, output_info):
                    try:
                        stream = audio.open(
                            format=pyaudio.paFloat32,
                            channels=channels,
                            rate=rate,
                            input=True,
                            output=True,
                            input_device_index=input_index,
                            output_device_index=output_index,
                            frames_per_buffer=block_size,
                            start=False,
                        )
                        selected_rate = rate
                        selected_channels = channels
                        break
                    except Exception as exc:
                        attempts.append(f"{rate}Hz/{channels}ch: {type(exc).__name__}: {exc}")
                if stream is not None:
                    break
            if stream is None:
                raise RuntimeError(
                    "无法以共同采样率打开 DSP 输入/输出：" + "；".join(attempts[-6:])
                )

            self._stream = stream
            stream.start_stream()
            with self._lock:
                self._state.update(
                    {
                        "status": "running",
                        "running": True,
                        "started_at": dsp.utc_iso(),
                        "stream_engine": "pyaudiowpatch-single-full-duplex",
                        "stream_input_name": str(input_info.get("name") or input_device.get("name") or ""),
                        "stream_output_name": str(output_info.get("name") or output_device.get("name") or ""),
                        "sample_rate": selected_rate,
                        "channels": selected_channels,
                        "block_size": block_size,
                        "estimated_latency_ms": round(block_size / selected_rate * 2000.0, 2),
                        "monitor_error": None,
                        "blocks_processed": 0,
                    }
                )
            self._apply_vtube_target(output_microphone)
            self._startup.set()

            while not self._stop.is_set():
                raw = stream.read(block_size, exception_on_overflow=False)
                flat = np.frombuffer(raw, dtype=np.float32)
                expected = block_size * selected_channels
                if flat.size < expected:
                    flat = np.pad(flat, (0, expected - flat.size))
                elif flat.size > expected:
                    flat = flat[:expected]
                original = flat.reshape((-1, selected_channels)).T.copy()

                with self._lock:
                    board = self._active_board()
                processed = board(original, selected_rate, reset=False)
                processed = _normalise_processed(processed, selected_channels, block_size)
                try:
                    stream.write(processed.T.tobytes(), exception_on_underflow=False)
                except TypeError:
                    stream.write(processed.T.tobytes())

                with self._lock:
                    self._state["input_dbfs"] = dsp._dbfs(original)
                    self._state["input_peak_dbfs"] = dsp._peak_dbfs(original)
                    self._state["output_dbfs"] = dsp._dbfs(processed)
                    self._state["output_peak_dbfs"] = dsp._peak_dbfs(processed)
                    self._state["blocks_processed"] = int(
                        self._state.get("blocks_processed") or 0
                    ) + 1
                    self._append_recording("original", original)
                    self._append_recording("processed", processed)
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
                try:
                    audio.terminate()
                except Exception:
                    pass
            self._stream = None
            with self._lock:
                if self._state.get("status") != "failed":
                    self._state["status"] = "stopped"
                self._state["running"] = False
                self._state["stopped_at"] = dsp.utc_iso()
                recording = self._recording
                self._recording = None
                self._state["recording"] = False
            if recording:
                recording["event"].set()
            self._startup.set()

    manager_class._scan = scan
    manager_class.devices = devices
    manager_class.configure = configure
    manager_class.set_bypass = set_bypass
    manager_class._run_stream = run_stream
    manager_class._aliver_stable_single_portaudio_engine = True
