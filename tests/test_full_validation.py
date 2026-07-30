from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import full_validation


class FakeAudioManager:
    def list_devices(self):
        return {
            "virtual_pairs": [
                {
                    "family": "vb-cable",
                    "playback": {"index": 7, "name": "CABLE Input (VB-Audio Virtual Cable)"},
                    "microphone": {"index": 8, "name": "CABLE Output (VB-Audio Virtual Cable)"},
                    "loopback": {"index": 9, "name": "CABLE Input [Loopback]"},
                }
            ]
        }


def test_find_audio_pair_matches_vtube_microphone_name():
    devices, pair = full_validation._find_audio_pair(
        FakeAudioManager(),
        "CABLE Output (VB-Audio Virtual Cable)",
    )

    assert devices["virtual_pairs"]
    assert pair["family"] == "vb-cable"
    assert pair["playback"]["index"] == 7


def test_step_has_machine_readable_status():
    result = full_validation._step(
        "collector.wgc_preview",
        ok=False,
        status="failed",
        error="capture failed",
    )

    assert result["name"] == "collector.wgc_preview"
    assert result["ok"] is False
    assert result["error"] == "capture failed"
    assert result["finished_at"]


@pytest.mark.asyncio
async def test_full_validation_always_exports_zip_when_components_are_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(full_validation, "VALIDATION_DIR", tmp_path)
    agent = SimpleNamespace(
        system_info=lambda: {"bridge_version": "test", "platform": "test"},
        audio=FakeAudioManager(),
    )

    result = await full_validation.run_full_validation(agent, {})

    assert result["completed"] is True
    bundle = Path(result["path"])
    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        assert "validation-report.json" in archive.namelist()
    assert result["report"]["collector_steps"][0]["status"] == "missing"
    assert result["report"]["avatar"]["available"] is False
