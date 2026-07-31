from __future__ import annotations

from types import SimpleNamespace

from bridge import realtime_voice_dsp as dsp


def _row(key: str, name: str, family: str, kind: str) -> dict:
    return {
        "key": key,
        "name": name,
        "virtual_family": family,
        "kind": kind,
        "is_virtual": True,
        "default_sample_rate": 48000,
    }


def _scan(include_cable_b: bool = True) -> dict:
    raw_playback = _row("raw-playback", "CABLE Input", "vb-cable", "output")
    raw_microphone = _row("raw-mic", "CABLE Output", "vb-cable", "input")
    a_playback = _row("a-playback", "CABLE-A Input", "vb-cable-a", "output")
    a_microphone = _row("a-mic", "CABLE-A Output", "vb-cable-a", "input")
    pairs = [
        {
            "family": "vb-cable",
            "playback": raw_playback,
            "microphone": raw_microphone,
            "loopback": None,
        },
        {
            "family": "vb-cable-a",
            "playback": a_playback,
            "microphone": a_microphone,
            "loopback": None,
        },
    ]
    outputs = [raw_playback, a_playback]
    inputs = [raw_microphone, a_microphone]
    if include_cable_b:
        b_playback = _row("b-playback", "CABLE-B Input", "vb-cable-b", "output")
        b_microphone = _row("b-mic", "CABLE-B Output", "vb-cable-b", "input")
        outputs.append(b_playback)
        inputs.append(b_microphone)
        pairs.append(
            {
                "family": "vb-cable-b",
                "playback": b_playback,
                "microphone": b_microphone,
                "loopback": None,
            }
        )
    return {
        "input_devices": inputs,
        "output_devices": outputs,
        "loopback_devices": [],
        "virtual_pairs": pairs,
        "routes": {
            "configured": {
                "gpt_out": {"family": "vb-cable"},
                "gpt_in": {"family": "vb-cable-a"},
            },
            "gpt_out": {
                "capture": raw_microphone,
                "playback": raw_playback,
                "ready": True,
            },
            "gpt_in": {
                "microphone": a_microphone,
                "playback": a_playback,
                "ready": True,
            },
        },
    }


def test_recommendation_excludes_raw_and_gpt_in_cables() -> None:
    result = dsp.recommend_dsp_routes(_scan())

    assert result["ready"] is True
    assert result["input_family"] == "vb-cable"
    assert result["gpt_in_family"] == "vb-cable-a"
    assert result["output_family"] == "vb-cable-b"
    assert result["output_playback"]["key"] == "b-playback"
    assert result["forbidden_output_families"] == ["vb-cable", "vb-cable-a"]


def test_recommendation_blocks_start_without_third_isolated_cable() -> None:
    result = dsp.recommend_dsp_routes(_scan(include_cable_b=False))

    assert result["ready"] is False
    assert result["output_playback"] is None
    assert result["output_microphone"] is None
    assert any("CABLE-B" in warning for warning in result["warnings"])


def test_resolve_replaces_stale_cable_a_output_with_cable_b() -> None:
    scan = _scan()
    scan["dsp_recommendation"] = dsp.recommend_dsp_routes(scan)
    manager = SimpleNamespace(
        _config={
            "input_device_key": "raw-mic",
            "output_device_key": "a-playback",
        }
    )

    input_device, output_device, output_microphone, _ = dsp.RealtimeVoiceDSPManager._resolve(
        manager,
        scan,
    )

    assert input_device["virtual_family"] == "vb-cable"
    assert output_device["virtual_family"] == "vb-cable-b"
    assert output_microphone["virtual_family"] == "vb-cable-b"
