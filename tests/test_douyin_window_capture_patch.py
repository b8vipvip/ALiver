from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from bridge import douyin_window_capture_patch as patch


def test_relative_region_pixels_stays_inside_window():
    assert patch._relative_region_pixels(
        1280,
        720,
        {"x": 0.782, "y": 0.405, "width": 0.205, "height": 0.555},
    ) == (1000, 291, 262, 399)
    assert patch._relative_region_pixels(
        100,
        100,
        {"x": 0.99, "y": 0.99, "width": 0.5, "height": 0.5},
    ) == (99, 99, 1, 1)


def test_capture_target_window_prefers_window_surface_and_crops(monkeypatch):
    full = np.zeros((100, 200, 3), dtype=np.uint8)
    full[:, :, 0] = np.arange(200, dtype=np.uint8)
    screen_called = False

    monkeypatch.setattr(patch, "_print_window_capture", lambda *_args: full.copy())

    def screen_capture(_window):
        nonlocal screen_called
        screen_called = True
        return None

    monkeypatch.setattr(patch, "_visible_screen_capture", screen_capture)
    manager = SimpleNamespace(
        _config={"ocr_region": {"x": 0.5, "y": 0.2, "width": 0.25, "height": 0.4}},
        _lock=threading.RLock(),
        _state={},
    )
    window = SimpleNamespace(handle=123, width=200, height=100)

    image, crop, source, pixels = patch._capture_target_window(manager, window)

    assert source == "printwindow"
    assert screen_called is False
    assert image.shape == (100, 200, 3)
    assert crop.shape == (80, 50, 3)
    assert pixels == (100, 20, 50, 80)
    assert manager._state["capture_safety"] == "window_surface"


def test_capture_target_window_propagates_safe_occlusion_error(monkeypatch):
    monkeypatch.setattr(patch, "_print_window_capture", lambda *_args: None)

    def covered(_window):
        raise RuntimeError("直播伴侣被其他窗口遮挡")

    monkeypatch.setattr(patch, "_visible_screen_capture", covered)
    manager = SimpleNamespace(
        _config={"ocr_region": {}},
        _lock=threading.RLock(),
        _state={},
    )
    window = SimpleNamespace(handle=123, width=200, height=100)

    with pytest.raises(RuntimeError, match="遮挡"):
        patch._capture_target_window(manager, window)
