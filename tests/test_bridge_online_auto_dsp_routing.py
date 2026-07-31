from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.api.common import bridge_to_out

ROOT = Path(__file__).resolve().parents[1]


def test_bridge_output_uses_active_websocket_as_online_truth() -> None:
    row = SimpleNamespace(
        id="bridge-1",
        name="Windows Bridge",
        machine_name="desktop",
        version="0.12.0",
        capabilities_json="[]",
        metadata_json="{}",
        status="online",
        last_seen_at=None,
        created_at=None,
        updated_at=None,
    )

    stale = bridge_to_out(row, False)
    live = bridge_to_out(row, True)

    assert stale.connected is False
    assert stale.status == "offline"
    assert live.connected is True
    assert live.status == "online"


def test_voice_dsp_patch_auto_configures_three_isolated_cables() -> None:
    script = (ROOT / "app/static/realtime_voice_dsp_ui_patch.js").read_text(encoding="utf-8")

    assert "row.connected === true" in script
    assert "pairByFamily(data, 'vb-cable')" in script
    assert "pairByFamily(data, 'vb-cable-a')" in script
    assert "pairByFamily(data, 'vb-cable-b')" in script
    assert "audio.routes.save" in script
    assert "gpt_out_capture_key: raw.loopback.key" in script
    assert "gpt_in_playback_key: gptIn.playback.key" in script
    assert "input_device_key: raw.microphone.key" in script
    assert "output_device_key: processed.playback.key" in script
    assert "select.disabled = true" in script
    assert "record.addEventListener('click', recordCompare, true)" in script
