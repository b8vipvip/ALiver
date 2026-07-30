from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from bridge.douyin_ocr_result_patch import _as_sequence, _ocr_lines
from bridge.douyin_visible_collector import DouyinVisibleCollectorManager


def test_as_sequence_accepts_numpy_arrays_without_truth_value_evaluation():
    assert _as_sequence(np.array(["昵称", "评论"])) == ["昵称", "评论"]
    assert _as_sequence(np.array([], dtype=str)) == []


def test_ocr_lines_accepts_rapidocr_numpy_result(monkeypatch):
    class FakeCapture:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def grab(self, _region):
            return np.zeros((24, 48, 4), dtype=np.uint8)

    mss_module = ModuleType("mss")
    mss_module.mss = FakeCapture

    class FakeResult:
        txts = np.array(["小雪 你好呀"])
        scores = np.array([0.93], dtype=np.float32)
        boxes = np.array([[[1, 2], [20, 2], [20, 12], [1, 12]]], dtype=np.float32)

    class FakeRapidOCR:
        def __call__(self, _image):
            return FakeResult()

    rapidocr_module = ModuleType("rapidocr")
    rapidocr_module.RapidOCR = FakeRapidOCR

    monkeypatch.setitem(sys.modules, "mss", mss_module)
    monkeypatch.setitem(sys.modules, "rapidocr", rapidocr_module)

    manager = SimpleNamespace(
        _ocr_engine=None,
        _region_rect=lambda _window: (100, 200, 48, 24),
        _ocr_row=DouyinVisibleCollectorManager._ocr_row,
    )
    rows = _ocr_lines(manager, object())

    assert len(rows) == 1
    assert rows[0]["text"] == "小雪 你好呀"
    assert rows[0]["confidence"] == pytest.approx(0.93, abs=1e-5)
    assert rows[0]["bbox"] == [101.0, 202.0, 120.0, 212.0]
