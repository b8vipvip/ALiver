from __future__ import annotations

import json
from array import array
from pathlib import Path

from app.live_run_service import redact
from app.voice_service import STYLE_PRESETS, _speech_url, profile_catalog
from bridge.voice_tts import _scale_pcm16

ROOT = Path(__file__).resolve().parents[1]


def test_live_run_redaction_removes_secrets_recursively():
    value = {
        "api_key": "secret",
        "settings": {"token": "token-value", "normal": 123},
        "rows": [{"password": "hidden", "name": "safe"}],
    }

    cleaned = redact(value)

    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["settings"]["token"] == "***REDACTED***"
    assert cleaned["settings"]["normal"] == 123
    assert cleaned["rows"][0]["password"] == "***REDACTED***"
    assert cleaned["rows"][0]["name"] == "safe"
    json.dumps(cleaned, allow_nan=False)


def test_voice_catalog_contains_safe_young_style_and_two_modes():
    catalog = profile_catalog()

    assert "sweet_young" in STYLE_PRESETS
    assert "不模仿任何具体真人" in STYLE_PRESETS["sweet_young"]["instruction"]
    assert "chatgpt_live" in catalog["modes"]
    assert "api_tts" in catalog["modes"]
    assert "Maple" in catalog["chatgpt_native_voices"]
    assert "shimmer" in catalog["tts_voices"]


def test_speech_url_accepts_base_or_complete_endpoint():
    assert _speech_url("https://api.openai.com/v1") == "https://api.openai.com/v1/audio/speech"
    assert _speech_url("https://example.test/v1/audio/speech") == "https://example.test/v1/audio/speech"


def test_pcm16_volume_scaling_clamps_samples():
    values = array("h", [1000, -1000, 20000, -20000])
    scaled = array("h")
    scaled.frombytes(_scale_pcm16(values.tobytes(), 2.0))

    assert list(scaled) == [2000, -2000, 32767, -32768]


def test_extension_collects_completed_assistant_text_for_diagnostics():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    capture = (ROOT / "chrome_extension" / "assistant_capture.js").read_text(encoding="utf-8")

    assert "assistant_capture.js" in manifest["content_scripts"][0]["js"]
    assert "assistant-completed" in capture
    assert "data-message-author-role=\"assistant\"" in capture
    assert "X-Extension-Token" in capture
