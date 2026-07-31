from __future__ import annotations

from typing import Any

from bridge import douyin_region_occlusion_patch as region_capture
from bridge import douyin_three_channel_patch as three_channel
from bridge import douyin_visible_collector as collector
from bridge import douyin_window_capture_patch as window_capture

_BASE_CAPTURE_TARGET: Any = None


def _screen_region_clear_capture(self: Any, window: Any):
    region = self._config.get("ocr_region") or collector.DEFAULT_CONFIG["ocr_region"]
    pixel_region = window_capture._relative_region_pixels(
        int(getattr(window, "width", 0) or 0),
        int(getattr(window, "height", 0) or 0),
        dict(region),
    )
    image = region_capture._screen_capture_if_region_clear(self, window, pixel_region)
    if image is None:
        raise RuntimeError("未遮挡互动区屏幕截图没有取得有效画面")

    crop_left, crop_top, crop_width, crop_height = window_capture._relative_region_pixels(
        int(image.shape[1]),
        int(image.shape[0]),
        dict(region),
    )
    crop = image[crop_top : crop_top + crop_height, crop_left : crop_left + crop_width].copy()
    if crop.size == 0:
        raise RuntimeError("安全屏幕兜底已取得窗口画面，但 OCR 区域超出范围")

    self._last_window_image = image
    self._last_region_image = crop
    with self._lock:
        self._state["capture_source"] = "screen_region_clear"
        self._state["last_capture_at"] = collector.utc_iso()
        self._state["last_capture_size"] = [int(image.shape[1]), int(image.shape[0])]
        self._state["last_region_pixels"] = [crop_left, crop_top, crop_width, crop_height]
        self._state["capture_safety"] = "ocr_region_unobstructed_screen_fallback"
        self._state["capture_foreground_required"] = False
        self._state["last_error"] = None
    return image, crop, "screen_region_clear", (crop_left, crop_top, crop_width, crop_height)


def _capture_target_window(self: Any, window: Any):
    if _BASE_CAPTURE_TARGET is None:
        raise RuntimeError("WGC 安全兜底尚未安装")

    try:
        return _BASE_CAPTURE_TARGET(self, window)
    except Exception as primary_exc:
        try:
            result = _screen_region_clear_capture(self, window)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"{primary_exc}；严格 OCR 区域屏幕兜底失败：{type(fallback_exc).__name__}: {fallback_exc}"
            ) from primary_exc

        with self._lock:
            self._state["wgc_fallback_reason"] = f"{type(primary_exc).__name__}: {primary_exc}"
        return result


def install_douyin_wgc_safe_fallback_patch() -> None:
    global _BASE_CAPTURE_TARGET
    manager = collector.DouyinVisibleCollectorManager
    if getattr(manager, "_aliver_wgc_safe_fallback_patch", False):
        return

    _BASE_CAPTURE_TARGET = window_capture._capture_target_window
    window_capture._capture_target_window = _capture_target_window
    three_channel._capture_target_window = _capture_target_window
    manager._capture_target_window = _capture_target_window
    manager._aliver_wgc_safe_fallback_patch = True
