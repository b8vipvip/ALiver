from __future__ import annotations

import math
import threading
from types import SimpleNamespace

import numpy as np

from bridge.realtime_voice_dsp_granular_patch import (
    GranularPitchBoard,
    StreamingGranularPitchShifter,
)


def _dominant_frequency(samples: np.ndarray, sample_rate: int) -> float:
    mono = samples[0]
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    frequencies = np.fft.rfftfreq(len(mono), d=1.0 / sample_rate)
    return float(frequencies[int(np.argmax(spectrum))])


def test_streaming_granular_pitch_tracks_requested_ratio_across_blocks() -> None:
    sample_rate = 48000
    semitones = 3.0
    frequency = 440.0
    total_frames = sample_rate * 2
    timeline = np.arange(total_frames, dtype=np.float32) / sample_rate
    source = np.sin(2.0 * math.pi * frequency * timeline).astype(np.float32)
    stereo = np.stack((source, source))

    shifter = StreamingGranularPitchShifter(
        sample_rate=sample_rate,
        channels=2,
        semitones=semitones,
    )
    blocks = []
    for start in range(0, total_frames, 512):
        blocks.append(shifter.process(stereo[:, start : start + 512]))
    rendered = np.concatenate(blocks, axis=1)

    # Discard the short delay-line priming region.
    analysed = rendered[:, int(sample_rate * 0.2) :]
    measured = _dominant_frequency(analysed, sample_rate)
    expected = frequency * (2.0 ** (semitones / 12.0))
    assert abs(measured - expected) < 4.0
    assert np.max(np.abs(rendered)) <= 1.01


def test_streaming_granular_pitch_has_no_block_boundary_reset_spikes() -> None:
    sample_rate = 48000
    frames = sample_rate
    timeline = np.arange(frames, dtype=np.float32) / sample_rate
    source = (0.25 * np.sin(2.0 * math.pi * 220.0 * timeline)).astype(np.float32)
    stereo = np.stack((source, source))

    shifter = StreamingGranularPitchShifter(
        sample_rate=sample_rate,
        channels=2,
        semitones=1.5,
    )
    blocks = []
    for start in range(0, frames, 256):
        blocks.append(shifter.process(stereo[:, start : start + 256]))
    rendered = np.concatenate(blocks, axis=1)[0]

    derivative = np.abs(np.diff(rendered))
    boundary_indices = np.arange(256, len(rendered), 256) - 1
    boundary_jumps = derivative[boundary_indices]
    normal_p99 = float(np.quantile(derivative, 0.99))
    assert float(np.quantile(boundary_jumps, 0.99)) <= normal_p99 * 1.5


class _IdentityBoard:
    def __call__(self, samples, sample_rate, *, reset=False):
        del sample_rate, reset
        return np.asarray(samples, dtype=np.float32)


def test_granular_board_reports_stateful_mode_and_fixed_shape() -> None:
    manager = SimpleNamespace(
        _lock=threading.RLock(),
        _state={"io_latency_ms": 20.0},
    )
    board = GranularPitchBoard(manager, _IdentityBoard(), semitones=1.5)
    source = np.ones((2, 512), dtype=np.float32) * 0.1

    result = board(source, 48000, reset=False)

    assert result.shape == source.shape
    assert manager._state["processing_mode"] == "streaming-granular-delay"
    assert manager._state["pitch_algorithm_latency_ms"] > 0
    assert manager._state["estimated_latency_ms"] > 20.0
