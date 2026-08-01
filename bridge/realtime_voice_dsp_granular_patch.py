from __future__ import annotations

import math
from typing import Any

import numpy as np

from bridge import realtime_voice_dsp as dsp


class StreamingGranularPitchShifter:
    """Low-latency stateful dual-read-head pitch shifter.

    The processor keeps one continuous circular delay line for the entire live
    stream. Two fractional read heads run half a grain apart and are Hann
    cross-faded, so delay-wrap discontinuities happen only while the affected
    head is silent. Unlike the earlier window renderer, no audio block is
    independently restarted or stitched back into the stream.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        semitones: float,
        grain_ms: float = 45.0,
        minimum_delay_ms: float = 10.0,
    ) -> None:
        self.sample_rate = max(8000, int(sample_rate))
        self.channels = max(1, int(channels))
        self.semitones = float(semitones)
        self.ratio = 2.0 ** (self.semitones / 12.0)
        self.grain_frames = max(
            64,
            int(round(self.sample_rate * grain_ms / 1000.0)),
        )
        self.minimum_delay_frames = max(
            8,
            int(round(self.sample_rate * minimum_delay_ms / 1000.0)),
        )
        self.maximum_delay_frames = self.minimum_delay_frames + self.grain_frames
        self.algorithm_latency_ms = round(
            self.maximum_delay_frames / self.sample_rate * 1000.0,
            2,
        )
        self._buffer_size = self.maximum_delay_frames + 8
        self._buffer = np.zeros(
            (self.channels, self._buffer_size),
            dtype=np.float32,
        )
        self._write_index = 0
        self._phase = 0.0
        self._phase_increment = (1.0 - self.ratio) / self.grain_frames
        self._frames_seen = 0

    @property
    def priming(self) -> bool:
        return self._frames_seen < self.maximum_delay_frames

    def reset(self) -> None:
        self._buffer.fill(0.0)
        self._write_index = 0
        self._phase = 0.0
        self._frames_seen = 0

    def _read(self, delay_frames: float) -> np.ndarray:
        position = (self._write_index - delay_frames) % self._buffer_size
        left = int(math.floor(position))
        fraction = position - left
        right = (left + 1) % self._buffer_size
        return (
            self._buffer[:, left] * (1.0 - fraction)
            + self._buffer[:, right] * fraction
        )

    def process(self, samples: np.ndarray) -> np.ndarray:
        value = np.asarray(samples, dtype=np.float32)
        if value.ndim == 1:
            value = value[np.newaxis, :]
        if value.ndim != 2:
            value = np.reshape(value, (1, -1))
        if value.shape[0] != self.channels:
            if value.shape[1] == self.channels:
                value = value.T
            elif value.shape[0] == 1 and self.channels == 2:
                value = np.repeat(value, 2, axis=0)
            elif value.shape[0] > self.channels:
                value = value[: self.channels]
            else:
                value = np.pad(
                    value,
                    ((0, self.channels - value.shape[0]), (0, 0)),
                )

        output = np.zeros_like(value, dtype=np.float32)
        for frame_index in range(value.shape[1]):
            self._buffer[:, self._write_index] = value[:, frame_index]

            phase_a = self._phase % 1.0
            phase_b = (phase_a + 0.5) % 1.0
            delay_a = self.minimum_delay_frames + phase_a * self.grain_frames
            delay_b = self.minimum_delay_frames + phase_b * self.grain_frames
            tap_a = self._read(delay_a)
            tap_b = self._read(delay_b)

            weight_a = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase_a)
            output[:, frame_index] = (
                weight_a * tap_a + (1.0 - weight_a) * tap_b
            )

            self._write_index = (self._write_index + 1) % self._buffer_size
            self._phase = (self._phase + self._phase_increment) % 1.0
            self._frames_seen += 1

        return np.ascontiguousarray(output, dtype=np.float32)


class GranularPitchBoard:
    """Apply streaming pitch first, then the stateful non-pitch Pedalboard."""

    def __init__(
        self,
        manager: Any,
        effect_board: Any,
        *,
        semitones: float,
    ) -> None:
        self.manager = manager
        self.effect_board = effect_board
        self.semitones = float(semitones)
        self._signature: tuple[int, int] | None = None
        self._pitch: StreamingGranularPitchShifter | None = None

    def _ensure_pitch(self, channels: int, sample_rate: int) -> None:
        signature = (channels, sample_rate)
        if self._pitch is not None and self._signature == signature:
            return
        self._pitch = StreamingGranularPitchShifter(
            sample_rate=sample_rate,
            channels=channels,
            semitones=self.semitones,
        )
        self._signature = signature

    def __call__(
        self,
        samples: np.ndarray,
        sample_rate: float,
        *,
        reset: bool = False,
    ) -> np.ndarray:
        source = np.asarray(samples, dtype=np.float32)
        if source.ndim == 1:
            source = source[np.newaxis, :]
        channels = source.shape[0]
        rate = int(round(float(sample_rate)))

        if abs(self.semitones) >= 0.01:
            self._ensure_pitch(channels, rate)
            assert self._pitch is not None
            if reset:
                self._pitch.reset()
            shifted = self._pitch.process(source)
            priming = self._pitch.priming
            algorithm_latency = self._pitch.algorithm_latency_ms
            grain_frames = self._pitch.grain_frames
            ratio = self._pitch.ratio
        else:
            shifted = source
            priming = False
            algorithm_latency = 0.0
            grain_frames = 0
            ratio = 1.0

        rendered = self.effect_board(shifted, sample_rate, reset=reset)
        result = np.asarray(rendered, dtype=np.float32)
        if result.ndim == 1:
            result = result[np.newaxis, :]
        if result.shape != source.shape:
            fixed = np.zeros_like(source)
            rows = min(fixed.shape[0], result.shape[0])
            columns = min(fixed.shape[1], result.shape[1])
            fixed[:rows, :columns] = result[:rows, :columns]
            result = fixed

        with self.manager._lock:
            io_latency = float(self.manager._state.get("io_latency_ms") or 0.0)
            self.manager._state.update(
                {
                    "stream_engine": (
                        "pyaudiowpatch-single-full-duplex-granular"
                    ),
                    "processing_mode": (
                        "streaming-granular-delay"
                        if abs(self.semitones) >= 0.01
                        else "pedalboard-stateful-no-pitch"
                    ),
                    "pitch_algorithm_latency_ms": algorithm_latency,
                    "pitch_grain_frames": grain_frames,
                    "pitch_ratio": round(ratio, 6),
                    "pitch_priming": priming,
                    "estimated_latency_ms": round(
                        io_latency + algorithm_latency,
                        2,
                    ),
                }
            )
        return np.ascontiguousarray(
            np.clip(result, -1.0, 1.0),
            dtype=np.float32,
        )


def install_realtime_voice_dsp_granular_patch() -> None:
    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_granular_pitch_patch", False):
        return

    original_make_board = manager_class._make_board

    def make_board(self: Any):
        semitones = float(self._config.get("pitch_semitones") or 0.0)
        if abs(semitones) < 0.01:
            board = original_make_board(self)
            return GranularPitchBoard(self, board, semitones=0.0)

        # Build the existing EQ/compressor/limiter chain without Pedalboard's
        # offline PitchShift. The live pitch stage is handled by the continuous
        # granular delay line above.
        with self._lock:
            stored = self._config.get("pitch_semitones")
            self._config["pitch_semitones"] = 0.0
            try:
                board = original_make_board(self)
            finally:
                self._config["pitch_semitones"] = stored
        return GranularPitchBoard(self, board, semitones=semitones)

    original_status = manager_class.status

    def status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        input_dbfs = float(value.get("input_dbfs") or -96.0)
        output_dbfs = float(value.get("output_dbfs") or -96.0)
        input_active = input_dbfs > -70.0
        output_active = output_dbfs > -70.0
        priming = bool(value.get("pitch_priming"))
        value["signal_diagnosis"] = {
            "input_active": input_active,
            "output_active": output_active,
            "input_without_output": bool(
                input_active and not output_active and not priming
            ),
            "priming": priming,
            "message": (
                "颗粒变调器正在填充连续延迟线，短暂静音属于正常启动延迟。"
                if input_active and not output_active and priming
                else "DSP 输入已有声音，但处理后输出仍为静音。"
                if input_active and not output_active
                else "DSP 输入与输出信号正常。"
                if input_active and output_active
                else "当前未检测到 DSP 输入声音。"
            ),
        }
        return value

    manager_class._make_board = make_board
    manager_class.status = status
    manager_class._aliver_granular_pitch_patch = True
