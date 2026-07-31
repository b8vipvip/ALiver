from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from bridge import douyin_wgc_hwnd_patch as patch


def test_capture_constructor_prefers_validated_hwnd_over_title():
    calls = []

    class FakeWindowsCapture:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    session = patch.HwndWindowsGraphicsCaptureSession("直播伴侣", 133302)
    session._new_capture(FakeWindowsCapture)

    assert calls
    assert calls[0]["window_hwnd"] == 133302
    assert "window_name" not in calls[0]
    assert session.target_mode == "window_hwnd"


def test_session_identity_keeps_handle_and_title(monkeypatch):
    manager = SimpleNamespace(
        _aliver_wgc_session=None,
        _aliver_wgc_identity=None,
        _lock=threading.RLock(),
        _state={},
    )
    window = SimpleNamespace(handle=133302, title="直播伴侣")
    monkeypatch.setattr(patch, "_root_hwnd", lambda value: int(value))

    session = patch._session_for_window(manager, window)

    assert session.window_hwnd == 133302
    assert manager._aliver_wgc_identity == (133302, "直播伴侣")
    assert manager._state["wgc_target_hwnd_hex"] == "0x208B6"
    assert manager._state["wgc_target_mode"] == "window_hwnd"


def test_exact_window_printwindow_fallback_does_not_enable_desktop_capture(monkeypatch):
    manager = SimpleNamespace(
        _config={"ocr_region": {"x": 0.5, "y": 0.25, "width": 0.4, "height": 0.5}},
        _lock=threading.RLock(),
        _state={},
        _last_window_image=None,
        _last_region_image=None,
    )
    window = SimpleNamespace(handle=133302, title="直播伴侣", width=100, height=80)

    def fail_wgc(_manager, _window):
        raise RuntimeError("GraphicsCaptureItem conversion failed")

    image = np.full((80, 100, 3), 127, dtype=np.uint8)
    monkeypatch.setattr(patch, "_ORIGINAL_CAPTURE_TARGET", fail_wgc)
    monkeypatch.setattr(patch, "_window_surface_fallback", lambda _manager, _window: image)

    full, crop, source, pixels = patch._capture_target_window(manager, window)

    assert source == "printwindow"
    assert full.shape == (80, 100, 3)
    assert crop.shape == (40, 40, 3)
    assert pixels == (50, 20, 40, 40)
    assert manager._state["capture_safety"] == "printwindow_exact_hwnd_surface"
    assert manager._state["capture_foreground_required"] is False
