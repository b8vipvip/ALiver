from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge import agent
from bridge import realtime_voice_dsp as dsp
from bridge import realtime_voice_dsp_preset_library_patch as library

ROOT = Path(__file__).resolve().parents[1]


def test_additional_builtin_voice_versions_are_available() -> None:
    expected = {
        "clear_girl",
        "soft_sweet",
        "cute_energy",
        "soft_whisper",
        "bright_streamer",
        "cool_woman",
        "warm_sister",
        "steady_male",
    }

    assert expected.issubset(dsp.DSP_PRESETS)
    snapshot = library.preset_library()
    assert snapshot["preset_meta"]["clear_girl"]["name"] == "清透少女"
    assert snapshot["preset_meta"]["warm_sister"]["name"] == "温柔姐姐"
    assert snapshot["preset_meta"]["steady_male"]["kind"] == "builtin"


def test_custom_voice_can_be_saved_renamed_and_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = tmp_path / "voice-presets.json"
    monkeypatch.setattr(library, "CUSTOM_PRESETS_PATH", storage)
    library._CUSTOM_IDS.clear()

    created = library.save_custom_preset(
        "我的自然少女 01",
        {
            "pitch_semitones": 1.7,
            "tone_age": 32,
            "presence_db": 1.4,
            "limiter_threshold_db": -1,
        },
    )
    preset_id = created["saved"]["id"]

    assert preset_id.startswith("user-")
    assert created["saved"]["name"] == "我的自然少女 01"
    assert preset_id in dsp.DSP_PRESETS
    assert storage.exists()
    document = json.loads(storage.read_text(encoding="utf-8"))
    assert document["presets"][preset_id]["values"]["pitch_semitones"] == 1.7

    updated = library.save_custom_preset(
        "我的自然少女 02",
        {
            "pitch_semitones": 2.1,
            "tone_age": 38,
            "presence_db": 1.8,
            "limiter_threshold_db": -1,
        },
        preset_id,
    )
    assert updated["saved"]["id"] == preset_id
    assert updated["saved"]["name"] == "我的自然少女 02"
    assert dsp.DSP_PRESETS[preset_id]["pitch_semitones"] == 2.1

    deleted = library.delete_custom_preset(preset_id)
    assert deleted["deleted"]["id"] == preset_id
    assert preset_id not in dsp.DSP_PRESETS
    assert deleted["custom_count"] == 0


def test_builtin_voice_cannot_be_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "CUSTOM_PRESETS_PATH", tmp_path / "voice-presets.json")
    with pytest.raises(ValueError, match="内置声音不可删除"):
        library.delete_custom_preset("natural_girl")


def test_bridge_exposes_voice_library_commands() -> None:
    capabilities = agent.BridgeAgent.capabilities()
    assert "audio.dsp.presets" in capabilities
    assert "audio.dsp.preset.save" in capabilities
    assert "audio.dsp.preset.delete" in capabilities


def test_voice_library_ui_is_loaded_and_exposes_management_actions() -> None:
    loader = (ROOT / "app/static/gpt_in_speech_patch.js").read_text(encoding="utf-8")
    script = (ROOT / "app/static/dsp_preset_library_ui.js").read_text(encoding="utf-8")

    assert "/static/dsp_preset_library_ui.js" in loader
    assert "另存为新声音" in script
    assert "覆盖当前声音" in script
    assert "audio.dsp.preset.save" in script
    assert "audio.dsp.preset.delete" in script
    assert "我的声音" in script
