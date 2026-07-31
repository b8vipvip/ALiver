from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.native_voice_tuning import normalize_native_tuning, render_native_instruction

ROOT = Path(__file__).resolve().parents[1]


def test_native_voice_tuning_clamps_ranges():
    value = normalize_native_tuning(
        {
            "pitch": 200,
            "pace": 20,
            "sweetness": -10,
            "clarity": "88",
        }
    )

    assert value["pitch"] == 100
    assert value["pace"] == 75
    assert value["sweetness"] == 0
    assert value["clarity"] == 88


def test_native_voice_instruction_uses_pitch_and_pace_without_tts():
    row = SimpleNamespace(
        native_tuning_json=(
            '{"pitch":78,"pace":112,"sweetness":80,"brightness":75,'
            '"energy":70,"warmth":45,"clarity":85,"expressiveness":72,"pause":35}'
        ),
        style_instruction="声音自然，不要过度卖萌。",
    )

    instruction = render_native_instruction(row)

    assert "声音自然，不要过度卖萌" in instruction
    assert "声线听感明亮偏高" in instruction
    assert "语速较快" in instruction
    assert "不要模仿任何具体真人" in instruction


def test_console_refinement_moves_providers_and_stacks_director_panels():
    script = (ROOT / "app" / "static" / "console_refinement_v4.js").read_text(encoding="utf-8")
    style = (ROOT / "app" / "static" / "console_refinement_v4.css").read_text(encoding="utf-8")

    assert 'button[data-tab="providers"]' in script
    assert "avatar-provider-subpanel" in script
    assert "director-plan-wide" in script
    assert "grid-template-columns: minmax(0, 1fr) !important" in style


def test_bridge_startup_waits_for_server_instead_of_exiting():
    source = (ROOT / "bridge" / "startup_retry_patch.py").read_text(encoding="utf-8")

    assert "Bridge 不会退出" in source
    assert "httpx.HTTPError" in source
    assert "await asyncio.wait_for" in source


def test_session_restore_only_targets_interrupted_bridge_sessions():
    source = (ROOT / "app" / "bridge_session_restore_patch.py").read_text(encoding="utf-8")

    assert 'AvatarSession.status == "interrupted"' in source
    assert "bridge_reconnected" in source
    assert "session.auto_restored" in source
