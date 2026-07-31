from __future__ import annotations

from pathlib import Path

import numpy as np

from bridge import realtime_voice_dsp as dsp
from bridge.realtime_voice_dsp_stable_engine_patch import (
    _candidate_rates,
    _normalise_processed,
)

ROOT = Path(__file__).resolve().parents[1]


def test_candidate_rates_prefers_config_and_deduplicates() -> None:
    rates = _candidate_rates(
        {"sample_rate": 48000},
        {"defaultSampleRate": 48000.0},
        {"defaultSampleRate": 44100.0},
    )

    assert rates == [48000, 44100]


def test_processed_audio_is_channel_and_frame_safe() -> None:
    mono = np.ones((1, 3), dtype=np.float32)
    result = _normalise_processed(mono, channels=2, frames=5)

    assert result.shape == (2, 5)
    assert result.dtype == np.float32
    assert np.all(result[:, :3] == 1.0)
    assert np.all(result[:, 3:] == 0.0)


def test_stable_engine_replaces_dual_portaudio_runtime() -> None:
    manager = dsp.RealtimeVoiceDSPManager

    assert manager._aliver_stable_single_portaudio_engine is True
    assert manager._run_stream.__module__.endswith("realtime_voice_dsp_stable_engine_patch")
    assert manager.devices.__module__.endswith("realtime_voice_dsp_stable_engine_patch")
    assert manager.configure.__module__.endswith("realtime_voice_dsp_stable_engine_patch")


def test_record_button_does_not_rescan_or_reconfigure_active_audio() -> None:
    script = (ROOT / "app/static/realtime_voice_dsp_ui_patch.js").read_text(encoding="utf-8")
    start = script.index("async function recordCompare")
    end = script.index("function install", start)
    recording_code = script[start:end]

    assert "await connectedBridge()" in recording_code
    assert "audio.dsp.status" in recording_code
    assert "audio.dsp.record_compare" in recording_code
    assert "autoConfigure(" not in recording_code
    assert "audio.dsp.devices" not in recording_code
    assert "录制期间不会扫描或重启音频设备" in recording_code


def test_engine_uses_one_full_duplex_pyaudio_stream() -> None:
    patch = (ROOT / "bridge/realtime_voice_dsp_stable_engine_patch.py").read_text(
        encoding="utf-8"
    )
    active_code = patch[patch.index("def run_stream"):]

    assert "input=True" in active_code
    assert "output=True" in active_code
    assert "pyaudiowpatch-single-full-duplex" in active_code
    assert "self._append_recording(\"original\", original)" in active_code
    assert "self._append_recording(\"processed\", processed)" in active_code
    assert "from pedalboard.io import AudioStream" not in active_code
    assert "self._start_monitor(" not in active_code
