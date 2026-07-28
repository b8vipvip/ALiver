from fractions import Fraction

from app.bootstrap import SERVER_VERSION
from app.providers.base import ProviderContext
from app.providers.simli import SimliProvider
from bridge.simli_diagnostics import (
    build_diagnostic_conclusion,
    estimate_signal_lag,
    median_fps,
    timeline_speed_ratio,
)
from bridge.simli_sync import clamp, frame_time_seconds, interleaved_pcm16


class FakeFrame:
    time = None
    pts = 2400
    time_base = Fraction(1, 48000)


class FakePacked:
    shape = (3, 2)
    ndim = 2

    def copy(self):
        return self

    def tobytes(self):
        return b"interleaved"


class FakePlanar:
    shape = (2, 3)
    ndim = 2
    T = FakePacked()

    def tobytes(self):
        return b"planar"


def test_frame_time_and_clamp_helpers():
    assert frame_time_seconds(FakeFrame()) == 0.05
    assert clamp("500", 80, 2000, 350) == 500
    assert clamp("invalid", 80, 2000, 350) == 350
    assert clamp(-1, 80, 2000, 350) == 80


def test_planar_audio_is_interleaved_before_playback():
    assert interleaved_pcm16(FakePlanar()) == b"interleaved"
    assert interleaved_pcm16(FakePacked()) == b"interleaved"


def test_provider_patch_forwards_sync_and_live_out_settings():
    provider = SimliProvider(
        ProviderContext(
            provider_id="simli-sync-test",
            name="Simli",
            provider_type="simli",
            api_base_url="https://api.simli.ai",
            credentials={"api_key": "secret"},
            settings={
                "face_id": "face-1",
                "audio_output_device_name": "CABLE-B Input (VB-Audio Cable B)",
                "auto_live_out": True,
                "sync_prebuffer_ms": 420,
                "video_delay_ms": 75,
                "late_video_drop_ms": 210,
            },
        )
    )

    config = provider._runtime_config()

    assert SERVER_VERSION == "0.7.1"
    assert config["audio_output_device_name"] == "CABLE-B Input (VB-Audio Cable B)"
    assert config["auto_live_out"] is True
    assert config["sync_prebuffer_ms"] == 420
    assert config["video_delay_ms"] == 75
    assert config["late_video_drop_ms"] == 210


def test_objective_lag_estimator_reports_video_delay():
    spans = [(1.0, 1.3), (2.4, 2.9), (4.1, 4.7), (6.6, 7.2), (9.0, 10.0)]

    def envelope(at: float) -> float:
        return 1.0 if any(start <= at <= end for start, end in spans) else 0.05

    audio = []
    video = []
    for index in range(260):
        at = index * 0.05
        audio.append((at, envelope(at)))
        video.append((at, envelope(at - 0.4)))

    result = estimate_signal_lag(audio, video)

    assert result["lag_ms"] == 400.0
    assert result["confidence"] == "high"
    assert result["correlation"] > 0.9


def test_slow_motion_and_pts_metrics_are_objective():
    assert median_fps([1 / 15] * 20) == 15.0
    assert median_fps([1 / 30] * 20) == 30.0
    assert timeline_speed_ratio([(0.0, 0.0), (10.0, 5.0)]) == 0.5

    conclusion, problems, suggestions = build_diagnostic_conclusion(
        {
            "first_onset_offset_ms": 1400.0,
            "estimated_lip_sync_offset_ms": 1250.0,
            "correlation_confidence": "high",
            "video_playback_speed_ratio": 0.5,
            "source_pts_fps": 15.0,
        }
    )

    assert "口型比声音晚" in conclusion
    assert any("慢放" in problem for problem in problems)
    assert suggestions
