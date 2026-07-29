from __future__ import annotations

import math
import time
from typing import Any

from bridge import vtube_motion


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _mouth_parameter(payload: dict[str, Any], preferred: str | None = None) -> tuple[str, float] | None:
    rows = payload.get("parameters")
    if not isinstance(rows, list):
        return None
    candidates = [preferred, "ParamMouthOpenY", "MouthOpenY"]
    names = [str(name).casefold() for name in candidates if name]
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name.casefold() in names:
            try:
                return name, abs(float(row.get("value", 0.0)))
            except (TypeError, ValueError):
                return name, 0.0
    return None


def install_vtube_motion_v2_patch() -> None:
    engine_class = vtube_motion.VTubeMotionEngine
    if getattr(engine_class, "_aliver_motion_v2", False):
        return

    original_init = engine_class.__init__
    original_poll_voice = engine_class._poll_voice
    original_values_for = engine_class._values_for
    original_status = engine_class.status

    def patched_init(
        self: Any,
        client: Any,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None,
    ) -> None:
        original_init(self, client, config, capabilities)
        self._speech_started_monotonic = 0.0
        self._speech_stopped_monotonic = 0.0
        self._previous_speaking = False
        self._speech_motion_gain = 0.0
        self._speech_signal_source = "voice_parameter"
        self._last_mouth_fallback_poll = 0.0
        self._mouth_fallback_parameter = "ParamMouthOpenY"

    async def patched_poll_voice(self: Any, now: float) -> None:
        previous = bool(getattr(self, "_speaking", False))
        await original_poll_voice(self, now)
        threshold = float(self.config.get("speech_threshold", 0.08))
        self._speech_signal_source = "voice_parameter"

        # Some VTube Studio audio setups animate ParamMouthOpenY while VoiceVolume
        # remains close to zero. Poll the model output at a conservative 4 Hz only
        # while the primary signal is quiet, then use it as the speech trigger.
        if (
            float(getattr(self, "_voice_value", 0.0)) < threshold
            and now - float(getattr(self, "_last_mouth_fallback_poll", 0.0)) >= 0.25
            and hasattr(self.client, "live2d_parameters")
        ):
            self._last_mouth_fallback_poll = now
            try:
                payload = await self.client.live2d_parameters()
                match = _mouth_parameter(payload, getattr(self, "_mouth_fallback_parameter", None))
                if match is not None:
                    self._mouth_fallback_parameter, mouth_value = match
                    fallback_threshold = max(0.025, threshold * 0.55)
                    if mouth_value >= fallback_threshold:
                        self._voice_value = max(float(self._voice_value), mouth_value)
                        self._last_voice_at = now
                        hold = float(self.config["speech_hold_ms"]) / 1000.0
                        self._speaking = now - self._last_voice_at <= hold
                        self._speech_signal_source = "live2d_mouth"
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"

        current = bool(getattr(self, "_speaking", False))
        if current and not previous:
            self._speech_started_monotonic = now
        elif previous and not current:
            self._speech_stopped_monotonic = now
        self._previous_speaking = current

    def idle_values(self: Any, now: float) -> dict[str, float]:
        elapsed = now - self._started_monotonic
        preset_gain = 1.12 if self.config.get("preset") == "lively" else 1.0
        intensity = float(self.config["idle_intensity"]) * preset_gain
        values: dict[str, float] = {}

        # Layered incommensurate waves avoid the obvious metronome-like left/right swing.
        self._put(
            values,
            "angle_x",
            intensity * (
                2.1 * math.sin(elapsed * 0.31)
                + 0.9 * math.sin(elapsed * 0.73 + 1.2)
                + 0.35 * math.sin(elapsed * 1.37 + 0.4)
            ),
        )
        self._put(
            values,
            "angle_y",
            intensity * (
                1.25 * math.sin(elapsed * 0.23 + 0.8)
                + 0.55 * math.sin(elapsed * 0.61 + 2.1)
            ),
        )
        self._put(
            values,
            "angle_z",
            intensity * (
                1.65 * math.sin(elapsed * 0.19 + 2.2)
                + 0.65 * math.sin(elapsed * 0.47 + 0.3)
            ),
        )
        self._put(values, "position_x", intensity * 0.055 * math.sin(elapsed * 0.17 + 1.4))
        self._put(
            values,
            "position_y",
            intensity * (
                0.12 * math.sin(elapsed * 0.42)
                + 0.045 * math.sin(elapsed * 0.93 + 0.7)
            ),
        )
        self._put(values, "mouth_smile", 0.08 + intensity * 0.025 * math.sin(elapsed * 0.35))
        return values

    def talking_values(self: Any, now: float) -> dict[str, float]:
        elapsed = now - self._started_monotonic
        threshold = float(self.config.get("speech_threshold", 0.08))
        voice = max(0.0, float(getattr(self, "_voice_value", 0.0)))
        voice_drive = _clamp((voice - threshold) / max(0.12, 0.45 - threshold), 0.0, 1.0)
        preset_gain = 1.38 if self.config.get("preset") == "lively" else 1.16
        intensity = float(self.config["talking_intensity"]) * preset_gain
        speech_gain = intensity * (1.36 + 0.34 * voice_drive)
        self._speech_motion_gain = speech_gain

        onset_age = now - float(getattr(self, "_speech_started_monotonic", 0.0))
        onset = 0.0
        if 0.0 <= onset_age <= 0.85:
            onset = math.sin(math.pi * onset_age / 0.85)

        # Short non-periodic emphasis pulses resemble conversational nods rather than constant swaying.
        nod_pulse = max(0.0, math.sin(elapsed * 1.83 + 0.55)) ** 5
        side_pulse = max(0.0, math.sin(elapsed * 0.91 + 2.2)) ** 7
        values: dict[str, float] = {}
        self._put(
            values,
            "angle_x",
            speech_gain * (
                3.9 * math.sin(elapsed * 0.72)
                + 1.8 * math.sin(elapsed * 1.47 + 0.9)
                + 2.8 * nod_pulse
                + 2.1 * onset
            ),
        )
        self._put(
            values,
            "angle_y",
            speech_gain * (
                2.25 * math.sin(elapsed * 0.49 + 1.1)
                + 1.15 * math.sin(elapsed * 1.19 + 2.4)
                - 1.6 * onset
            ),
        )
        self._put(
            values,
            "angle_z",
            speech_gain * (
                2.8 * math.sin(elapsed * 0.37 + 2.0)
                + 1.35 * math.sin(elapsed * 0.97 + 0.2)
                + 1.7 * side_pulse
            ),
        )
        self._put(
            values,
            "position_x",
            speech_gain * (
                0.075 * math.sin(elapsed * 0.43 + 1.3)
                + 0.035 * math.sin(elapsed * 1.11)
            ),
        )
        self._put(
            values,
            "position_y",
            speech_gain * (
                0.22 * math.sin(elapsed * 0.82)
                + 0.14 * nod_pulse
                + 0.18 * onset
            ),
        )
        self._put(values, "mouth_smile", _clamp(0.15 + voice_drive * 0.26 + nod_pulse * 0.08, 0.0, 0.55))
        self._put(values, "brow_y_left", _clamp(0.08 + voice_drive * 0.16 + onset * 0.10, -1.0, 1.0))
        self._put(values, "brow_y_right", _clamp(0.08 + voice_drive * 0.16 + onset * 0.10, -1.0, 1.0))
        return values

    def patched_values_for(self: Any, mode: str, now: float) -> dict[str, float]:
        if mode == "idle":
            self._speech_motion_gain = 0.0
            return idle_values(self, now)
        if mode == "talking":
            return talking_values(self, now)
        return original_values_for(self, mode, now)

    def patched_status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        value.update(
            {
                "algorithm_version": 2,
                "speech_motion_gain": round(float(getattr(self, "_speech_motion_gain", 0.0)), 3),
                "speech_signal_source": str(
                    getattr(self, "_speech_signal_source", "voice_parameter")
                ),
                "mouth_fallback_parameter": str(
                    getattr(self, "_mouth_fallback_parameter", "ParamMouthOpenY")
                ),
                "speech_started_monotonic": round(
                    float(getattr(self, "_speech_started_monotonic", 0.0)), 3
                ),
            }
        )
        return value

    engine_class.__init__ = patched_init
    engine_class._poll_voice = patched_poll_voice
    engine_class._values_for = patched_values_for
    engine_class.status = patched_status
    engine_class._aliver_motion_v2 = True
