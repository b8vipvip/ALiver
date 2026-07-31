from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from bridge import douyin_wgc_safe_fallback_patch as patch


def _manager():
    return SimpleNamespace(
        _config={"ocr_region": {"x": 0.5, "y": 0.25, "width": 0.4, "height": 0.5}},
        _lock=threading.RLock(),
        _state={},
        _last_window_image=None,
        _last_region_image=None,
    )


def test_wgc_failure_uses_unobstructed_ocr_region_fallback(monkeypatch):
    manager = _manager()
    window = SimpleNamespace(handle=133302, title="直播伴侣", left=10, top=20, width=100, height=80)
    image = np.full((80, 100, 3), 127, dtype=np.uint8)
    calls = []

    def fail_wgc(_manager, _window):
        raise RuntimeError("Failed to convert item to GraphicsCaptureItem")

    def safe_screen(_manager, _window, pixel_region):
        calls.append(pixel_region)
        return image

    monkeypatch.setattr(patch, "_BASE_CAPTURE_TARGET", fail_wgc)
    monkeypatch.setattr(patch.region_capture, "_screen_capture_if_region_clear", safe_screen)

    full, crop, source, pixels = patch._capture_target_window(manager, window)

    assert calls == [(50, 20, 40, 60)]
    assert source == "screen_region_clear"
    assert full.shape == (80, 100, 3)
    assert crop.shape == (60, 40, 3)
    assert pixels == (50, 20, 40, 60)
    assert manager._state["capture_safety"] == "ocr_region_unobstructed_screen_fallback"
    assert "GraphicsCaptureItem" in manager._state["wgc_fallback_reason"]


def test_blocked_region_preserves_primary_and_fallback_errors(monkeypatch):
    manager = _manager()
    window = SimpleNamespace(handle=133302, title="直播伴侣", left=10, top=20, width=100, height=80)

    def fail_wgc(_manager, _window):
        raise RuntimeError("WGC conversion failed")

    def blocked(_manager, _window, _pixel_region):
        raise RuntimeError("互动区被浏览器覆盖")

    monkeypatch.setattr(patch, "_BASE_CAPTURE_TARGET", fail_wgc)
    monkeypatch.setattr(patch.region_capture, "_screen_capture_if_region_clear", blocked)

    with pytest.raises(RuntimeError) as error:
        patch._capture_target_window(manager, window)

    message = str(error.value)
    assert "WGC conversion failed" in message
    assert "严格 OCR 区域屏幕兜底失败" in message
    assert "互动区被浏览器覆盖" in message
