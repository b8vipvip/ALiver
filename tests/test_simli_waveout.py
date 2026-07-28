import pytest

from bridge.simli_waveout import WAVE_MAPPER, choose_waveout_device


def test_waveout_matches_winmm_truncated_device_name():
    devices = [
        {
            "index": 4,
            "name": "CABLE-B Input (VB-Audio Cable B",
            "channels": 2,
            "is_mapper": False,
        }
    ]

    selected = choose_waveout_device(
        devices,
        preferred_name="CABLE-B Input (VB-Audio Cable B)",
    )

    assert selected["index"] == 4


def test_waveout_auto_live_out_prefers_cable_b():
    devices = [
        {"index": 1, "name": "Speakers", "channels": 2, "is_mapper": False},
        {"index": 3, "name": "CABLE Input (VB-Audio Virtual Cable)", "channels": 2, "is_mapper": False},
        {"index": 8, "name": "CABLE-B Input (VB-Audio Cable B)", "channels": 2, "is_mapper": False},
    ]

    selected = choose_waveout_device(devices, auto_live_out=True)

    assert selected["index"] == 8


def test_waveout_missing_configured_device_fails_clearly():
    with pytest.raises(RuntimeError, match="LIVE_OUT"):
        choose_waveout_device(
            [{"index": 1, "name": "Speakers", "channels": 2, "is_mapper": False}],
            preferred_name="CABLE-B Input",
        )


def test_waveout_uses_windows_mapper_when_no_live_out_exists():
    selected = choose_waveout_device(
        [{"index": 1, "name": "Speakers", "channels": 2, "is_mapper": False}],
        auto_live_out=True,
    )

    assert selected["index"] == WAVE_MAPPER
    assert selected["is_mapper"] is True
