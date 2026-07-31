from __future__ import annotations

import re
from typing import Any

from bridge import audio_capture


def install_audio_device_family_patch() -> None:
    if getattr(audio_capture, "_aliver_extended_virtual_families", False):
        return
    original = audio_capture.virtual_family

    def virtual_family(name: str) -> str | None:
        value = audio_capture.normalize_device_name(name)
        direct = original(name)
        if direct:
            return direct
        match = re.search(r"\bcable[- ]?([a-d])\s+(?:input|output|in|out)\b", value, re.I)
        if match:
            return f"vb-cable-{match.group(1).lower()}"
        match = re.search(r"\bcable\s+(?:in|out)\s*(\d+)ch\b", value, re.I)
        if match:
            return f"vb-cable-{match.group(1)}ch"
        if "hi-fi cable" in value:
            return "vb-hifi-cable"
        return None

    audio_capture.virtual_family = virtual_family
    audio_capture._aliver_extended_virtual_families = True
