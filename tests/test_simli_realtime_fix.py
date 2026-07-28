import ctypes
import threading
from collections import deque
from types import SimpleNamespace

from bridge import simli_waveout
from bridge.simli_realtime_fix import (
    BufferedWindowsWaveOutStream,
    fast_interpolate,
    lightweight_renderer_status,
    lightweight_runtime_status,
)


class FakeQueue:
    def __init__(self, size: int) -> None:
        self.size = size

    def qsize(self) -> int:
        return self.size


class FakeRenderer:
    def __init__(self) -> None:
        self._metrics = {
            "status": "active",
            "video_frames_rendered": 25,
            "av_offset_ms": 10.0,
        }
        self._audio_buffer_seconds = 0.12
        self._audio_queue = FakeQueue(1)
        self._video_queue = FakeQueue(2)
        self._last_video_clock = 2.0
        self._started_monotonic = 1.0
        self._diag_video_pts_deltas = deque([0.04] * 30)
        self._diag_video_arrival_deltas = deque([0.04] * 30)
        self._diag_video_render_deltas = deque([0.04] * 30)
        self._diag_speed_samples = deque([(0.0, 0.0), (2.0, 2.0)])
        self._diag_last_report = None
        self._tuning = {"clock_mode": "source_pts"}
        self.full_report_calls = 0

    def _audio_playhead(self) -> float:
        return 2.0

    def _diag_diagnostic_report(self):
        self.full_report_calls += 1
        raise AssertionError("Realtime status must never run the full correlation report")

    def _tuning_snapshot(self):
        return {"settings": {"clock_mode": "source_pts"}, "latest_test": None}


def test_fast_interpolate_matches_linear_interpolation():
    samples = [(0.0, 0.0), (1.0, 10.0), (2.0, 20.0)]

    assert fast_interpolate(samples, 0.5) == 5.0
    assert fast_interpolate(samples, 1.5) == 15.0
    assert fast_interpolate(samples, -1.0) is None
    assert fast_interpolate(samples, 3.0) is None


def test_lightweight_renderer_status_never_runs_full_report(monkeypatch):
    renderer = FakeRenderer()
    monkeypatch.setattr("bridge.simli_realtime_fix.time.monotonic", lambda: 3.0)

    result = lightweight_renderer_status(renderer)

    assert renderer.full_report_calls == 0
    assert result["status_mode"] == "lightweight_realtime"
    assert result["source_pts_fps"] == 25.0
    assert result["render_fps_recent"] == 25.0
    assert result["video_playback_speed_ratio"] == 1.0
    assert result["audio_queue_size"] == 1
    assert result["video_queue_size"] == 2
    assert result["sync_health"] == "good"


def test_lightweight_runtime_status_contains_reconciliation_flags(monkeypatch):
    renderer = FakeRenderer()
    monkeypatch.setattr("bridge.simli_realtime_fix.time.monotonic", lambda: 3.0)
    runtime = SimpleNamespace(
        state={"status": "failed", "session_id": "session-1"},
        renderer=renderer,
        renderer_task=SimpleNamespace(done=lambda: True),
        sender_task=SimpleNamespace(done=lambda: False),
        capture_thread=SimpleNamespace(is_alive=lambda: False),
    )

    result = lightweight_runtime_status(runtime)

    assert result["status"] == "failed"
    assert result["renderer_task_done"] is True
    assert result["sender_task_done"] is False
    assert result["capture_thread_alive"] is False
    assert result["renderer"]["status_mode"] == "lightweight_realtime"
    assert renderer.full_report_calls == 0


class FakeWinmm:
    def __init__(self) -> None:
        self.prepare_calls = 0
        self.write_calls = 0
        self.unprepare_calls = 0

    def waveOutPrepareHeader(self, _handle, _header, _size):
        self.prepare_calls += 1
        return simli_waveout.MMSYSERR_NOERROR

    def waveOutWrite(self, _handle, _header, _size):
        self.write_calls += 1
        return simli_waveout.MMSYSERR_NOERROR

    def waveOutUnprepareHeader(self, _handle, _header, _size):
        self.unprepare_calls += 1
        return simli_waveout.MMSYSERR_NOERROR

    def waveOutReset(self, _handle):
        return simli_waveout.MMSYSERR_NOERROR

    def waveOutClose(self, _handle):
        return simli_waveout.MMSYSERR_NOERROR


def make_buffered_stream() -> tuple[BufferedWindowsWaveOutStream, FakeWinmm]:
    stream = BufferedWindowsWaveOutStream.__new__(BufferedWindowsWaveOutStream)
    dll = FakeWinmm()
    stream._dll = dll
    stream._lock = threading.RLock()
    stream._handle = ctypes.c_void_p(1)
    stream._closed = False
    stream.sample_rate = 48000
    stream.channels = 2
    stream.bits_per_sample = 16
    stream._pending_buffers = deque()
    stream._pending_seconds = 0.0
    stream._max_pending_seconds = 0.18
    return stream, dll


def test_buffered_waveout_submits_next_chunk_without_waiting_for_previous_done():
    stream, dll = make_buffered_stream()
    twenty_ms_stereo_pcm = b"\x00" * 3840

    stream.write(twenty_ms_stereo_pcm)
    stream.write(twenty_ms_stereo_pcm)

    assert dll.write_calls == 2
    assert len(stream._pending_buffers) == 2
    assert 0.039 <= stream._pending_seconds <= 0.041
