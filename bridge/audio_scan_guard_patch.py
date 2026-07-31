from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from typing import Any

from bridge import audio_capture

_SCAN_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = 2.0


def _guarded_scan(manager: Any, original: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Keep PyAudioWPatch device enumeration out of concurrent native calls.

    PortAudio initialization can terminate the whole Windows process with an
    access violation when multiple Bridge commands create PyAudio instances at
    the same time. A short per-manager cache also collapses the startup burst
    from the audio-route and DSP workspaces into one native scan.
    """
    with _SCAN_LOCK:
        now = time.monotonic()
        cached = getattr(manager, "_aliver_audio_scan_cache", None)
        cached_at = float(getattr(manager, "_aliver_audio_scan_cache_at", 0.0) or 0.0)
        if isinstance(cached, dict) and now - cached_at <= _CACHE_TTL_SECONDS:
            return copy.deepcopy(cached)

        result = original()
        manager._aliver_audio_scan_cache = copy.deepcopy(result)
        manager._aliver_audio_scan_cache_at = time.monotonic()
        return copy.deepcopy(result)


def install_audio_scan_guard_patch() -> None:
    manager_class = audio_capture.AudioCaptureManager
    if getattr(manager_class, "_aliver_audio_scan_guard_patch", False):
        return

    original = manager_class.list_devices

    def list_devices(self: Any) -> dict[str, Any]:
        return _guarded_scan(self, lambda: original(self))

    manager_class.list_devices = list_devices
    manager_class._aliver_audio_scan_guard_patch = True
