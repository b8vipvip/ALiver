from __future__ import annotations

from typing import Any

from bridge import douyin_visible_collector as collector

_BASE_UPDATE_CONFIG = collector.DouyinVisibleCollectorManager.update_config
_ALLOWED_MODES = {"hybrid", "uia", "ocr", "electron", "wgc"}


def _update_config(
    self: Any,
    values: dict[str, Any],
    *,
    persist: bool = True,
) -> dict[str, Any]:
    payload = dict(values or {})
    requested_mode = str(payload.get("mode") or self._config.get("mode") or "hybrid").strip().lower()
    if requested_mode not in _ALLOWED_MODES:
        requested_mode = "hybrid"

    # The original 0.12 collector only knows hybrid/uia/ocr. Let it validate all
    # shared fields, then restore the two explicit three-channel diagnostic modes.
    normalized = dict(payload)
    if requested_mode in {"electron", "wgc"}:
        normalized["mode"] = "hybrid"
    _BASE_UPDATE_CONFIG(self, normalized, persist=False)

    with self._lock:
        self._config["mode"] = requested_mode
        self._config["enable_electron_accessibility"] = bool(
            self._config.get("enable_electron_accessibility", True)
        )
        self._config["enable_windows_graphics_capture"] = bool(
            self._config.get("enable_windows_graphics_capture", True)
        )
        self._config["allow_screen_capture_fallback"] = bool(
            self._config.get("allow_screen_capture_fallback", False)
        )
        self._config["wgc_frame_timeout_seconds"] = max(
            0.5,
            min(float(self._config.get("wgc_frame_timeout_seconds") or 3.0), 10.0),
        )
        if persist:
            self._save_config()
    return self.status()


def install_douyin_three_channel_config_patch() -> None:
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_three_channel_config_patch", False):
        return
    manager.update_config = _update_config
    manager._aliver_three_channel_config_patch = True
