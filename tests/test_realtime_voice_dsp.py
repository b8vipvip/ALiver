from pathlib import Path

from bridge.realtime_voice_dsp import (
    apply_preset,
    match_stream_device_name,
    normalize_dsp_config,
    recommend_dsp_routes,
)

ROOT = Path(__file__).resolve().parents[1]


def test_dsp_config_clamps_unsafe_values():
    value = normalize_dsp_config(
        {
            "pitch_semitones": 99,
            "tone_age": -999,
            "block_size": 777,
            "compressor_ratio": 0,
            "limiter_threshold_db": 8,
        }
    )

    assert value["pitch_semitones"] == 8.0
    assert value["tone_age"] == -100.0
    assert value["block_size"] == 1024
    assert value["compressor_ratio"] == 1.0
    assert value["limiter_threshold_db"] == 0.0


def test_sweet_young_preset_changes_real_dsp_values():
    value = apply_preset(normalize_dsp_config({}), "sweet_young")

    assert value["preset"] == "sweet_young"
    assert value["pitch_semitones"] == 3.0
    assert value["tone_age"] == 58.0
    assert value["bass_db"] < 0
    assert value["presence_db"] > 0


def test_pedalboard_device_name_matching_tolerates_suffixes():
    candidates = [
        "CABLE Output (VB-Audio Virtual Cable)",
        "Speakers (Realtek High Definition Audio)",
    ]

    assert (
        match_stream_device_name(candidates, "CABLE Output (VB-Audio Virtual Cable) [Input]")
        == "CABLE Output (VB-Audio Virtual Cable)"
    )


def test_route_recommendation_uses_third_cable_for_processed_output():
    standard_microphone = {
        "key": "std-mic",
        "name": "CABLE Output (VB-Audio Virtual Cable)",
        "virtual_family": "vb-cable",
    }
    standard_playback = {
        "key": "std-play",
        "name": "CABLE Input (VB-Audio Virtual Cable)",
        "virtual_family": "vb-cable",
    }
    cable_b_playback = {
        "key": "b-play",
        "name": "CABLE-B Input (VB-Audio Cable B)",
        "virtual_family": "vb-cable-b",
    }
    cable_b_microphone = {
        "key": "b-mic",
        "name": "CABLE-B Output (VB-Audio Cable B)",
        "virtual_family": "vb-cable-b",
    }
    scan = {
        "routes": {
            "gpt_out": {
                "family": "vb-cable",
                "playback": standard_playback,
            },
            "gpt_in": {
                "family": "vb-cable-a",
                "microphone": {
                    "name": "CABLE-A Output (VB-Audio Cable A)",
                    "virtual_family": "vb-cable-a",
                },
            },
        },
        "virtual_pairs": [
            {
                "family": "vb-cable",
                "loopback": {"key": "std-loop"},
                "playback": standard_playback,
                "microphone": standard_microphone,
            },
            {
                "family": "vb-cable-a",
                "loopback": {"key": "a-loop"},
                "playback": {"key": "a-play"},
                "microphone": {"key": "a-mic"},
            },
            {
                "family": "vb-cable-b",
                "loopback": {"key": "b-loop"},
                "playback": cable_b_playback,
                "microphone": cable_b_microphone,
            },
        ],
    }

    result = recommend_dsp_routes(scan)

    assert result["ready"] is True
    assert result["input_family"] == "vb-cable"
    assert result["input_microphone"]["key"] == "std-mic"
    assert result["output_family"] == "vb-cable-b"
    assert result["output_playback"]["key"] == "b-play"
    assert result["output_microphone"]["key"] == "b-mic"
    assert result["instructions"]["dsp_input"].startswith("CABLE Output")
    assert result["instructions"]["douyin_microphone"].startswith("CABLE-B Output")


def test_route_recommendation_warns_when_only_two_cables_exist():
    scan = {
        "routes": {
            "gpt_out": {"family": "vb-cable"},
            "gpt_in": {"family": "vb-cable-a"},
        },
        "virtual_pairs": [
            {
                "family": "vb-cable",
                "loopback": {"key": "std-loop"},
                "playback": {"key": "std-play"},
                "microphone": {"key": "std-mic"},
            },
            {
                "family": "vb-cable-a",
                "loopback": {"key": "a-loop"},
                "playback": {"key": "a-play"},
                "microphone": {"key": "a-mic"},
            },
        ],
    }

    result = recommend_dsp_routes(scan)

    assert result["ready"] is False
    assert any("CABLE-B" in message for message in result["warnings"])


def test_voice_lab_is_a_real_dsp_console_not_director_prompt_tuning():
    script = (ROOT / "app/static/native_voice_lab_v2.js").read_text(encoding="utf-8")
    app_init = (ROOT / "app/__init__.py").read_text(encoding="utf-8")

    assert "audio.dsp.start" in script
    assert "audio.dsp.record_compare" in script
    assert "更多操作 → 朗读" in script
    assert "/api/native-voice/profiles" not in script
    assert "install_voice_director_patch" not in app_init
