from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np

from bridge import douyin_three_channel_config_patch as config_patch
from bridge import douyin_three_channel_patch as patch


class DummyCollector:
    def __init__(self, *, uia=None, ocr=None, mode="hybrid") -> None:
        self._config = {
            "mode": mode,
            "confidence_threshold": 0.72,
            "capture_join_notices": False,
            "enable_electron_accessibility": True,
            "enable_windows_graphics_capture": True,
            "allow_screen_capture_fallback": False,
            "wgc_frame_timeout_seconds": 3.0,
            "ocr_region": {},
        }
        self._state = {"uia_available": True}
        self._lock = threading.RLock()
        self._uia = list(uia or [])
        self._ocr = list(ocr or [])
        self.saved = False

    def _uia_lines(self, _window):
        return list(self._uia)

    def _ocr_lines(self, _window):
        return list(self._ocr)

    def _save_config(self):
        self.saved = True

    def status(self):
        return {"config": dict(self._config)}


def comment(text: str, source: str) -> dict:
    return {"text": text, "source": source, "confidence": 1.0, "bbox": [1, 1, 20, 10]}


def test_frame_to_rgb_converts_common_bgra_layout():
    frame = SimpleNamespace(
        to_numpy=lambda copy=True: np.array([[[10, 20, 30, 255]]], dtype=np.uint8),
    )
    image = patch._frame_to_rgb(frame)
    assert image.shape == (1, 1, 3)
    assert image[0, 0].tolist() == [30, 20, 10]


def test_three_channel_prefers_standard_uia(monkeypatch):
    manager = DummyCollector(uia=[comment("小明: UIA优先", "uia")])
    electron_called = False

    def electron(*_args):
        nonlocal electron_called
        electron_called = True
        return [comment("小明: Electron", "electron_accessibility")]

    monkeypatch.setattr(patch, "electron_accessibility_lines", electron)
    lines = patch._collect_lines(manager, SimpleNamespace())

    assert lines[0]["text"] == "小明: UIA优先"
    assert manager._state["active_source"] == "uia"
    assert electron_called is False


def test_three_channel_falls_back_to_electron_accessibility(monkeypatch):
    manager = DummyCollector(uia=[comment("互动消息", "uia")])
    monkeypatch.setattr(
        patch,
        "electron_accessibility_lines",
        lambda *_args: [comment("小红: Electron消息", "electron_accessibility")],
    )

    lines = patch._collect_lines(manager, SimpleNamespace())

    assert lines[0]["text"] == "小红: Electron消息"
    assert manager._state["active_source"] == "electron_accessibility"
    assert [row["channel"] for row in manager._state["channel_trace"]] == ["uia", "electron_accessibility"]


def test_three_channel_falls_back_to_windows_graphics_capture(monkeypatch):
    manager = DummyCollector(
        uia=[comment("互动消息", "uia")],
        ocr=[comment("小云: WGC消息", "ocr")],
    )
    monkeypatch.setattr(patch, "electron_accessibility_lines", lambda *_args: [])

    lines = patch._collect_lines(manager, SimpleNamespace())

    assert lines[0]["source"] == "windows_graphics_capture"
    assert manager._state["active_source"] == "windows_graphics_capture"
    assert [row["channel"] for row in manager._state["channel_trace"]] == [
        "uia",
        "electron_accessibility",
        "windows_graphics_capture",
    ]


def test_explicit_wgc_mode_survives_legacy_config_validation():
    manager = DummyCollector(mode="hybrid")
    result = config_patch._update_config(
        manager,
        {
            "mode": "wgc",
            "wgc_frame_timeout_seconds": 99,
            "enable_windows_graphics_capture": True,
        },
    )

    assert manager._config["mode"] == "wgc"
    assert manager._config["wgc_frame_timeout_seconds"] == 10.0
    assert manager.saved is True
    assert result["config"]["mode"] == "wgc"
