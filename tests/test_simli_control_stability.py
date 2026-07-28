from types import SimpleNamespace

from bridge.simli_control_stability import fast_manager_tuning_status


class FakeQueue:
    def __init__(self, size):
        self._size = size

    def qsize(self):
        return self._size


class FakeRenderer:
    def __init__(self):
        self._metrics = {"status": "active", "av_offset_ms": 12.0}
        self._audio_buffer_seconds = 0.25
        self._audio_queue = FakeQueue(3)
        self._video_queue = FakeQueue(4)
        self._last_video_clock = 1.2

    def _audio_playhead(self):
        return 1.25

    def _tuning_snapshot(self):
        return {
            "settings": {"clock_mode": "source_pts", "target_fps": 30.0},
            "latest_test": None,
        }

    def status(self):
        raise AssertionError("lightweight status must not call full renderer.status()")


def test_fast_tuning_status_avoids_full_renderer_status():
    runtime = SimpleNamespace(
        session_id="session-1",
        state={"status": "active"},
        renderer=FakeRenderer(),
    )
    manager = SimpleNamespace(sessions={"session-1": runtime})

    result = fast_manager_tuning_status(manager, session_id="session-1")

    assert result["session_active"] is True
    assert result["session_id"] == "session-1"
    assert result["av_sync"]["status_source"] == "lightweight_snapshot"
    assert result["av_sync"]["audio_queue_size"] == 3
    assert result["av_sync"]["video_queue_size"] == 4
    assert result["av_sync"]["sync_health"] == "good"
