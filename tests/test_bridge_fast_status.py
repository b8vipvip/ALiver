from collections import deque

import pytest

from bridge.bridge_transport_guard import _send_json_safely
from bridge.simli_fast_status import fast_status_snapshot


class FakeQueue:
    def __init__(self, size):
        self._size = size

    def qsize(self):
        return self._size


class FakeRenderer:
    def __init__(self):
        self._metrics = {
            "status": "active",
            "video_frames_rendered": 30,
            "av_offset_ms": 12.0,
            "audio_output_backend": "winmm_waveout",
        }
        self._audio_buffer_seconds = 0.25
        self._audio_queue = FakeQueue(2)
        self._video_queue = FakeQueue(3)
        self._last_video_clock = 1.2
        self._started_monotonic = 1.0
        self._diag_video_pts_deltas = deque([0.04] * 40)
        self._diag_video_arrival_deltas = deque([0.04] * 40)
        self._diag_video_render_deltas = deque([0.04] * 40)
        self._diag_speed_samples = deque([(0.0, 0.0), (1.0, 1.0)])
        self._diag_last_report = None
        self.full_report_calls = 0

    def _audio_playhead(self):
        return 1.1

    def _diag_diagnostic_report(self):
        self.full_report_calls += 1
        raise AssertionError("Fast status must not execute full correlation analysis")

    def _tuning_snapshot(self):
        return {"settings": {"clock_mode": "source_pts"}, "latest_test": None}


def test_fast_status_does_not_run_full_diagnostics(monkeypatch):
    renderer = FakeRenderer()
    monkeypatch.setattr("bridge.simli_fast_status.time.monotonic", lambda: 2.0)

    result = fast_status_snapshot(renderer)

    assert renderer.full_report_calls == 0
    assert result["status_mode"] == "lightweight_snapshot"
    assert result["source_pts_fps"] == 25.0
    assert result["render_fps_recent"] == 25.0
    assert result["video_playback_speed_ratio"] == 1.0
    assert result["sync_health"] == "good"
    assert result["audio_queue_size"] == 2
    assert result["video_queue_size"] == 3


class BrokenWebSocket:
    async def send(self, _payload):
        raise OSError("socket closed")


@pytest.mark.asyncio
async def test_closed_websocket_response_is_dropped_without_raising():
    sent = await _send_json_safely(BrokenWebSocket(), {"type": "result"}, command_id="cmd-1")
    assert sent is False
