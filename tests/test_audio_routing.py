from bridge.audio_capture import (
    AudioCaptureManager,
    calculate_pcm16_levels,
    device_key,
    normalize_device_name,
    virtual_family,
)


def device(name, kind, family, *, loopback=False):
    return {
        "index": 1,
        "key": device_key(name, kind, loopback),
        "name": name,
        "kind": kind,
        "is_loopback": loopback,
        "is_virtual": True,
        "virtual_family": family,
        "input_channels": 2 if kind in {"input", "loopback"} else 0,
        "output_channels": 2 if kind == "output" else 0,
        "default_sample_rate": 48000,
    }


def test_pcm_levels_detect_signal():
    samples = (1000).to_bytes(2, "little", signed=True) * 100
    levels = calculate_pcm16_levels(samples)
    assert levels["peak"] == 1000
    assert levels["dbfs"] > -40


def test_virtual_family_recognizes_vb_cable_pairs():
    assert virtual_family("CABLE Input (VB-Audio Virtual Cable) [Loopback]") == "vb-cable"
    assert virtual_family("CABLE Output (VB-Audio Virtual Cable)") == "vb-cable"
    assert virtual_family("CABLE-A Input (VB-Audio Cable A)") == "vb-cable-a"
    assert virtual_family("CABLE-A Output (VB-Audio Cable A)") == "vb-cable-a"
    assert normalize_device_name("  CABLE Input [Loopback] ") == "cable input"


def test_recommendations_require_two_isolated_families():
    loopbacks = [
        device("CABLE Input [Loopback]", "loopback", "vb-cable", loopback=True),
        device("CABLE-A Input [Loopback]", "loopback", "vb-cable-a", loopback=True),
    ]
    inputs = [
        device("CABLE Output", "input", "vb-cable"),
        device("CABLE-A Output", "input", "vb-cable-a"),
    ]
    outputs = [
        device("CABLE Input", "output", "vb-cable"),
        device("CABLE-A Input", "output", "vb-cable-a"),
    ]
    pairs = AudioCaptureManager._build_virtual_pairs(loopbacks, inputs, outputs)
    recommendation = AudioCaptureManager._recommend_routes(pairs)
    assert recommendation["ready"] is True
    assert recommendation["isolated"] is True
    assert recommendation["gpt_out"]["family"] == "vb-cable"
    assert recommendation["gpt_in"]["family"] == "vb-cable-a"


def test_single_virtual_cable_is_not_ready():
    pair = {
        "family": "vb-cable",
        "loopback": {"key": "l"},
        "playback": {"key": "p"},
        "microphone": {"key": "m"},
    }
    recommendation = AudioCaptureManager._recommend_routes([pair])
    assert recommendation["ready"] is False
    assert recommendation["isolated"] is False
    assert recommendation["warnings"]
