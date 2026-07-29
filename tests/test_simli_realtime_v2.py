import asyncio
from types import SimpleNamespace

from bridge.simli_realtime_v2 import trim_idle_media


class Chunk:
    def __init__(self, duration: float):
        self.duration = duration


def test_idle_media_trim_reduces_old_audio_and_video_lookahead():
    audio_queue: asyncio.Queue[Chunk] = asyncio.Queue()
    for _ in range(230):
        audio_queue.put_nowait(Chunk(0.01))
    video_queue: asyncio.Queue[object] = asyncio.Queue()
    for index in range(31):
        video_queue.put_nowait(index)

    reanchors = []
    renderer = SimpleNamespace(
        _audio_queue=audio_queue,
        _video_queue=video_queue,
        _audio_buffer_seconds=2.3,
        _metrics={"source_pts_fps": 25.0},
        _tuning={"target_fps": 25.0},
        _tuning_reanchor=lambda: reanchors.append(True),
    )

    result = trim_idle_media(renderer, target_audio_ms=420, target_video_ms=500)

    assert 0.40 <= renderer._audio_buffer_seconds <= 0.43
    assert audio_queue.qsize() <= 43
    assert video_queue.qsize() == 12
    assert result["trimmed_audio_ms"] >= 1800
    assert result["dropped_video_frames"] == 19
    assert renderer._metrics["idle_trim_count"] == 1
    assert renderer._metrics["idle_trim_post_audio_buffer_ms"] <= 430
    assert reanchors == [True]


def test_idle_media_trim_is_noop_when_buffer_is_already_small():
    audio_queue: asyncio.Queue[Chunk] = asyncio.Queue()
    for _ in range(20):
        audio_queue.put_nowait(Chunk(0.01))
    video_queue: asyncio.Queue[object] = asyncio.Queue()
    for index in range(5):
        video_queue.put_nowait(index)

    renderer = SimpleNamespace(
        _audio_queue=audio_queue,
        _video_queue=video_queue,
        _audio_buffer_seconds=0.2,
        _metrics={"source_pts_fps": 25.0},
        _tuning={"target_fps": 25.0},
        _tuning_reanchor=lambda: (_ for _ in ()).throw(AssertionError("must not reanchor")),
    )

    result = trim_idle_media(renderer, target_audio_ms=420, target_video_ms=500)

    assert result["trimmed_audio_ms"] == 0.0
    assert audio_queue.qsize() == 20
    assert video_queue.qsize() == 5
    assert renderer._metrics["idle_trim_count"] == 0
