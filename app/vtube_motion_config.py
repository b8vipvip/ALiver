from __future__ import annotations

from typing import Any

MOTION_ACTIONS = ("idle", "talking", "thinking", "wave", "happy", "surprised", "reset")

DEFAULT_EXPRESSION_MAP = {
    "thinking": "",
    "happy": "",
    "surprised": "",
}

DEFAULT_MOTION_ENGINE = {
    "enabled": False,
    "preset": "gentle",
    "fps": 15,
    "auto_speech": True,
    "voice_parameter": "VoiceVolume",
    "speech_threshold": 0.08,
    "speech_hold_ms": 500,
    "idle_intensity": 0.55,
    "talking_intensity": 0.85,
    "action_intensity": 1.0,
    "expressions_enabled": True,
    "expression_map": dict(DEFAULT_EXPRESSION_MAP),
}


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def normalize_motion_engine(value: Any) -> dict[str, Any]:
    source = dict(value) if isinstance(value, dict) else {}
    preset = str(source.get("preset") or DEFAULT_MOTION_ENGINE["preset"]).strip().lower()
    if preset not in {"gentle", "lively"}:
        preset = "gentle"

    expression_map = dict(DEFAULT_EXPRESSION_MAP)
    raw_expressions = source.get("expression_map")
    if isinstance(raw_expressions, dict):
        for action in expression_map:
            expression_map[action] = str(raw_expressions.get(action) or "").strip()[:240]

    return {
        "enabled": bool(source.get("enabled", DEFAULT_MOTION_ENGINE["enabled"])),
        "preset": preset,
        "fps": _clamp_int(source.get("fps"), 5, 30, int(DEFAULT_MOTION_ENGINE["fps"])),
        "auto_speech": bool(source.get("auto_speech", DEFAULT_MOTION_ENGINE["auto_speech"])),
        "voice_parameter": str(
            source.get("voice_parameter") or DEFAULT_MOTION_ENGINE["voice_parameter"]
        ).strip()[:120],
        "speech_threshold": _clamp_float(
            source.get("speech_threshold"), 0.005, 0.95, float(DEFAULT_MOTION_ENGINE["speech_threshold"])
        ),
        "speech_hold_ms": _clamp_int(
            source.get("speech_hold_ms"), 100, 3000, int(DEFAULT_MOTION_ENGINE["speech_hold_ms"])
        ),
        "idle_intensity": _clamp_float(
            source.get("idle_intensity"), 0.0, 2.0, float(DEFAULT_MOTION_ENGINE["idle_intensity"])
        ),
        "talking_intensity": _clamp_float(
            source.get("talking_intensity"), 0.0, 2.0, float(DEFAULT_MOTION_ENGINE["talking_intensity"])
        ),
        "action_intensity": _clamp_float(
            source.get("action_intensity"), 0.0, 2.0, float(DEFAULT_MOTION_ENGINE["action_intensity"])
        ),
        "expressions_enabled": bool(
            source.get("expressions_enabled", DEFAULT_MOTION_ENGINE["expressions_enabled"])
        ),
        "expression_map": expression_map,
    }
