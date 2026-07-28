from fractions import Fraction

from app.bootstrap import SERVER_VERSION
from app.providers.base import ProviderContext
from app.providers.simli import SimliProvider
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

    assert SERVER_VERSION == "0.7.0"
    assert config["audio_output_device_name"] == "CABLE-B Input (VB-Audio Cable B)"
    assert config["auto_live_out"] is True
    assert config["sync_prebuffer_ms"] == 420
    assert config["video_delay_ms"] == 75
    assert config["late_video_drop_ms"] == 210
