from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from bridge import live_debug_recovery_patch as recovery


def test_candidate_score_rejects_hidden_or_cloaked_windows():
    visible = {
        "valid": True,
        "visible": True,
        "iconic": False,
        "cloaked": False,
        "width": 1280,
        "height": 720,
        "handle": 101,
        "title": "直播伴侣",
        "class_name": "Chrome_WidgetWin_1",
    }
    hidden = {**visible, "handle": 102, "visible": False}
    cloaked = {**visible, "handle": 103, "cloaked": True}

    assert recovery._candidate_score(visible, original=101) > 10_000
    assert recovery._candidate_score(hidden, original=102) == float("-inf")
    assert recovery._candidate_score(cloaked, original=103) == float("-inf")


def test_preview_failure_clears_historical_images(monkeypatch):
    manager = SimpleNamespace(
        _lock=threading.RLock(),
        _state={"capture_source": "screen_region_clear"},
        _last_window_image=object(),
        _last_region_image=object(),
    )

    def fail_preview(_manager):
        raise RuntimeError("capture failed")

    monkeypatch.setattr(recovery, "_BASE_PREVIEW", fail_preview)

    with pytest.raises(RuntimeError, match="capture failed"):
        recovery._patched_preview(manager)

    assert manager._last_window_image is None
    assert manager._last_region_image is None
    assert manager._state["capture_current"] is False
    assert "capture failed" in manager._state["capture_error"]


def test_live_validation_auto_starts_saved_collector(monkeypatch):
    class FakeCollector:
        def __init__(self):
            self.started = False
            self.scan_count = 0
            self.last_scan_at = None
            self.config = {"extension_id": "extension-1"}

        def status(self):
            return {
                "running": self.started,
                "scan_count": self.scan_count,
                "last_scan_at": self.last_scan_at,
                "last_error": None,
                "config": dict(self.config),
            }

        def update_config(self, values):
            self.config.update(values)
            return self.status()

        def start(self):
            self.started = True
            self.scan_count = 1
            self.last_scan_at = "2026-07-31T13:00:00+00:00"
            return self.status()

    async def base_run(_agent, _options):
        return [
            {
                "name": "live.collector_event",
                "phase": "live",
                "level": "passed",
                "status": "passed",
                "ok": True,
                "message": "event received",
                "data": {"event_type": "comment"},
            }
        ]

    collector = FakeCollector()
    monkeypatch.setattr(recovery, "_BASE_RUN_LIVE", base_run)
    steps = asyncio.run(
        recovery._run_live_with_auto_start(
            SimpleNamespace(douyin_collector=collector),
            {"live_timeout_seconds": 10},
        )
    )

    assert collector.started is True
    assert steps[0]["level"] == "passed"
    assert steps[0]["data"]["collector_auto_started"] is True
