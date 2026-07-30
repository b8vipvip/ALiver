from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bridge import douyin_validation_fix as fix


def test_frame_to_rgb_accepts_windows_capture_frame_buffer():
    frame = SimpleNamespace(
        frame_buffer=np.array([[[10, 20, 30, 255], [1, 2, 3, 255]]], dtype=np.uint8)
    )

    image = fix._frame_to_rgb(frame)

    assert image.tolist() == [[[30, 20, 10], [3, 2, 1]]]


def test_hwnd_capture_session_passes_exact_window_handle(monkeypatch):
    created: list[dict] = []

    class FakeWindowsCapture:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def event(self, callback):
            return callback

        def start_free_threaded(self):
            return SimpleNamespace(stop=lambda: None)

    fake_module = SimpleNamespace(
        Frame=object,
        InternalCaptureControl=object,
        WindowsCapture=FakeWindowsCapture,
    )
    monkeypatch.setitem(sys.modules, "windows_capture", fake_module)

    session = fix._HwndWindowsGraphicsCaptureSession(0x1234, "直播伴侣")
    session.start()

    assert created == [
        {
            "cursor_capture": False,
            "draw_border": False,
            "monitor_index": None,
            "window_hwnd": 0x1234,
        }
    ]
    assert session._started is True


def test_session_for_window_reuses_same_hwnd_session():
    manager = SimpleNamespace(_aliver_wgc_session=None, _aliver_wgc_identity=None)
    window = SimpleNamespace(handle=456, title="直播伴侣")

    first = fix._session_for_window(manager, window)
    second = fix._session_for_window(manager, window)

    assert first is second
    assert first.hwnd == 456


def test_accessibility_denial_creates_waiting_launcher(monkeypatch, tmp_path: Path):
    executable = tmp_path / "直播伴侣.exe"
    executable.write_bytes(b"")

    class AccessDenied(Exception):
        winerror = 5

    def denied(_manager):
        raise AccessDenied("拒绝访问")

    monkeypatch.setattr(fix, "_BASE_RESTART_ACCESSIBILITY", denied)
    monkeypatch.setattr(
        fix.three,
        "_selected_window_process",
        lambda _manager, _window: {
            "pid": 321,
            "process_path": str(executable),
        },
    )
    monkeypatch.setattr(
        fix.three,
        "electron_accessibility_status",
        lambda _manager, refresh=False: {
            "available": True,
            "enabled": False,
            "restart_required": True,
        },
    )
    monkeypatch.setattr(fix, "_launcher_path", lambda: tmp_path / "restart.cmd")
    manager = SimpleNamespace(
        _find_window=lambda: SimpleNamespace(handle=123),
        _lock=threading.RLock(),
        _state={},
    )

    result = fix.restart_electron_accessibility(manager)

    assert result["permission_mismatch"] is True
    assert result["manual_close_required"] is True
    launcher = Path(result["launcher_path"])
    assert launcher.exists()
    text = launcher.read_text(encoding="utf-8-sig")
    assert "--force-renderer-accessibility" in text
    assert "321" in text
