from __future__ import annotations

from typing import Any

import numpy as np

from bridge import realtime_voice_dsp as dsp


def _matrix(value: Any, channels: int, frames: int | None = None) -> np.ndarray:
    result = np.asarray(value, dtype=np.float32)
    if result.ndim == 1:
        result = result[np.newaxis, :]
    if result.ndim != 2:
        result = np.reshape(result, (1, -1))
    if result.shape[0] != channels and result.shape[1] == channels:
        result = result.T
    if result.shape[0] > channels:
        result = result[:channels]
    elif result.shape[0] == 1 and channels == 2:
        result = np.repeat(result, 2, axis=0)
    elif result.shape[0] < channels:
        result = np.pad(result, ((0, channels - result.shape[0]), (0, 0)))
    if frames is not None:
        if result.shape[1] > frames:
            result = result[:, :frames]
        elif result.shape[1] < frames:
            result = np.pad(result, ((0, 0), (0, frames - result.shape[1])))
    return np.ascontiguousarray(result, dtype=np.float32)


class _RealtimeBoardAdapter:
    """Turn Pedalboard's offline PitchShift into a smooth realtime processor.

    ``PitchShift`` buffers short calls when ``reset=False`` and returns no
    samples. The previous workaround called the complete effect board with
    ``reset=True`` for every 1024-frame block. That avoided silence, but it also
    restarted the pitch shifter roughly every 21 ms at 48 kHz, producing the
    clearly audible stutter and broken syllables reported on Windows.

    Pitch-enabled profiles now use 50% overlap-add. A longer contextual window
    is processed with ``reset=True`` and neighbouring windows are cross-faded.
    The live PortAudio loop still receives exactly one fixed-size block on every
    iteration. This adds a bounded algorithmic delay, but removes block-edge
    discontinuities and keeps speech intelligible.
    """

    def __init__(self, manager: Any, board: Any, *, pitch_enabled: bool) -> None:
        self.manager = manager
        self.board = board
        self.pitch_enabled = bool(pitch_enabled)
        self._signature: tuple[int, int, int] | None = None
        self._window_frames = 0
        self._hop_frames = 0
        self._window = np.empty(0, dtype=np.float32)
        self._input = np.empty((0, 0), dtype=np.float32)
        self._ola = np.empty((0, 0), dtype=np.float32)
        self._weights = np.empty(0, dtype=np.float32)
        self._output = np.empty((0, 0), dtype=np.float32)
        self._primed = False

    def _reset_stream(self, channels: int, frames: int, sample_rate: int) -> None:
        # Keep the pitch context close to 85 ms at normal 48 kHz settings. Very
        # large device blocks use two blocks to avoid unbounded CPU/latency.
        target = min(8192, max(4096, frames * 4))
        target = max(frames * 2, (target // frames) * frames)
        if (target // frames) % 2:
            target += frames
        self._window_frames = target
        self._hop_frames = target // 2
        self._window = np.hanning(target + 1)[:-1].astype(np.float32)
        self._input = np.empty((channels, 0), dtype=np.float32)
        self._ola = np.zeros((channels, target), dtype=np.float32)
        self._weights = np.zeros(target, dtype=np.float32)
        self._output = np.empty((channels, 0), dtype=np.float32)
        self._signature = (channels, frames, sample_rate)
        self._primed = False

        algorithm_ms = round(target / sample_rate * 1000.0, 2)
        with self.manager._lock:
            io_ms = float(
                self.manager._state.get("io_latency_ms")
                or self.manager._state.get("estimated_latency_ms")
                or 0.0
            )
            self.manager._state.update(
                {
                    "processing_mode": "pedalboard-overlap-add",
                    "pitch_window_frames": target,
                    "pitch_hop_frames": self._hop_frames,
                    "pitch_algorithm_latency_ms": algorithm_ms,
                    "pitch_priming": True,
                    "estimated_latency_ms": round(io_ms + algorithm_ms, 2),
                }
            )

    def _append_output(self, value: np.ndarray) -> None:
        self._output = (
            value.copy()
            if self._output.size == 0
            else np.concatenate((self._output, value), axis=1)
        )

    def _process_window(self, channels: int, sample_rate: float) -> None:
        segment = self._input[:, : self._window_frames]
        rendered = self.board(segment, sample_rate, reset=True)
        raw_rendered = np.asarray(rendered)
        if raw_rendered.size == 0 or not np.any(np.isfinite(raw_rendered)):
            rendered_matrix = segment.copy()
            with self.manager._lock:
                self.manager._state["empty_output_recoveries"] = int(
                    self.manager._state.get("empty_output_recoveries") or 0
                ) + 1
                self.manager._state["processing_mode"] = (
                    "pedalboard-overlap-add-dry-recovery"
                )
        else:
            rendered_matrix = _matrix(rendered, channels, self._window_frames)
        rendered_matrix = np.nan_to_num(
            rendered_matrix,
            nan=0.0,
            posinf=1.0,
            neginf=-1.0,
        )
        weighted = rendered_matrix * self._window[np.newaxis, :]
        self._ola += weighted
        self._weights += self._window

        weights = self._weights[: self._hop_frames]
        ready = np.zeros((channels, self._hop_frames), dtype=np.float32)
        np.divide(
            self._ola[:, : self._hop_frames],
            weights[np.newaxis, :],
            out=ready,
            where=weights[np.newaxis, :] > 1e-4,
        )
        self._append_output(np.clip(ready, -1.0, 1.0))

        hop = self._hop_frames
        self._ola[:, :-hop] = self._ola[:, hop:]
        self._ola[:, -hop:] = 0.0
        self._weights[:-hop] = self._weights[hop:]
        self._weights[-hop:] = 0.0
        self._input = self._input[:, hop:]
        self._primed = True

    def __call__(
        self,
        audio: np.ndarray,
        sample_rate: float,
        *,
        reset: bool = False,
    ) -> np.ndarray:
        source = np.asarray(audio, dtype=np.float32)
        if source.ndim == 1:
            source = source[np.newaxis, :]
        channels, frames = source.shape

        if not self.pitch_enabled:
            result = self.board(source, sample_rate, reset=reset)
            raw_result = np.asarray(result)
            if raw_result.size == 0:
                result = source
            with self.manager._lock:
                self.manager._state.update(
                    {
                        "processing_mode": "pedalboard-stateful",
                        "pitch_algorithm_latency_ms": 0.0,
                        "pitch_priming": False,
                    }
                )
            return _matrix(result, channels, frames)

        signature = (channels, frames, int(round(float(sample_rate))))
        if reset or self._signature != signature:
            self._reset_stream(*signature)

        self._input = (
            source.copy()
            if self._input.size == 0
            else np.concatenate((self._input, source), axis=1)
        )
        while self._input.shape[1] >= self._window_frames:
            self._process_window(channels, sample_rate)

        if self._output.shape[1] >= frames:
            result = self._output[:, :frames].copy()
            self._output = self._output[:, frames:]
        else:
            result = np.zeros((channels, frames), dtype=np.float32)

        with self.manager._lock:
            self.manager._state["pitch_priming"] = not self._primed
            self.manager._state["pitch_buffered_output_frames"] = int(
                self._output.shape[1]
            )
        return result


def install_realtime_voice_dsp_output_guard_patch() -> None:
    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_output_guard_patch", False):
        return

    original_make_board = manager_class._make_board
    original_status = manager_class.status

    def make_board(self: Any):
        board = original_make_board(self)
        pitch = abs(float(self._config.get("pitch_semitones") or 0.0))
        return _RealtimeBoardAdapter(self, board, pitch_enabled=pitch >= 0.01)

    def status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        input_dbfs = float(value.get("input_dbfs") or -96.0)
        output_dbfs = float(value.get("output_dbfs") or -96.0)
        input_active = input_dbfs > -70.0
        output_silent = output_dbfs <= -90.0
        priming = bool(value.get("pitch_priming"))
        input_without_output = bool(input_active and output_silent and not priming)
        value["signal_diagnosis"] = {
            "input_active": input_active,
            "output_active": output_dbfs > -70.0,
            "input_without_output": input_without_output,
            "priming": priming,
            "message": (
                "变调器正在填充上下文缓冲，短暂静音属于正常启动延迟。"
                if input_active and output_silent and priming
                else "DSP 输入已有声音，但处理后输出仍为静音。请检查处理引擎状态。"
                if input_without_output
                else "DSP 输入与输出信号正常。"
                if input_active and not output_silent
                else "当前未检测到 DSP 输入声音。"
            ),
        }
        value.setdefault("processing_mode", self._state.get("processing_mode"))
        value.setdefault(
            "empty_output_recoveries",
            int(self._state.get("empty_output_recoveries") or 0),
        )
        return value

    manager_class._make_board = make_board
    manager_class.status = status
    manager_class._aliver_output_guard_patch = True
