import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from bridge.simli_link_diagnostics_v2 import (
    atomic_write_json,
    build_history_report,
    build_link_diagnosis,
    manager_link_status,
)
from bridge.simli_link_timeline_v2 import build_pipeline_timeline


def test_arrival_burst_does_not_create_false_local_renderer_bottleneck():
    snapshot = {
        "rtc": {
            "available": True,
            "rtt_ms": 70,
            "route": "UDP",
            "video": {
                "jitter_ms": 4,
                "jitter_buffer_avg_ms": 35,
                "packet_loss_pct": 0.2,
                "decoded_fps": 12.5,
            },
            "audio": {"jitter_ms": 3, "packet_loss_pct": 0.1},
        },
        "aliver": {
            "receive_fps": 12.5,
            "render_fps": 12.0,
            "arrival_burst_fps": 32.3,
            "video_queue_size": 31,
            "video_queue_growth": 0,
            "video_queue_drops_delta": 0,
            "video_render_drops_delta": 0,
            "scheduler_lateness_ms": 4,
        },
        "audio": {
            "return_audio_buffer_ms": 180,
            "waveout_pending_ms": 170,
            "underflows_delta": 0,
        },
    }

    diagnosis = build_link_diagnosis(snapshot)

    assert diagnosis["primary_bottleneck"] == "healthy"
    assert diagnosis["scores"]["local_renderer"] == 0
    assert not any(issue["code"] == "local_renderer" for issue in diagnosis["issues"])


def test_network_and_audio_backlog_are_reported_as_concurrent_issues():
    snapshot = {
        "rtc": {
            "available": True,
            "rtt_ms": 137,
            "route": "UDP",
            "video": {
                "jitter_ms": 3,
                "jitter_buffer_avg_ms": 271,
                "packet_loss_pct": 18.8,
                "decoded_fps": 19,
            },
            "audio": {
                "jitter_ms": 2,
                "jitter_buffer_avg_ms": 47,
                "packet_loss_pct": 22.2,
            },
        },
        "aliver": {
            "receive_fps": 19,
            "render_fps": 18.5,
            "video_queue_size": 31,
            "video_queue_growth": 0,
            "video_queue_drops_delta": 0,
            "video_render_drops_delta": 0,
            "scheduler_lateness_ms": 3,
        },
        "audio": {
            "return_audio_buffer_ms": 2290,
            "waveout_pending_ms": 170,
            "underflows_delta": 0,
        },
    }

    diagnosis = build_link_diagnosis(snapshot)
    issue_codes = {issue["code"] for issue in diagnosis["issues"]}

    assert diagnosis["primary_bottleneck"] == "network"
    assert issue_codes == {"network", "audio_buffer"}
    assert "同时存在" in diagnosis["conclusion_zh"]


def test_timeline_ignores_idle_events_before_current_gpt_out_onset():
    renderer = SimpleNamespace(
        _diag_events=deque(
            [
                {"event": "first_non_silent_audio", "at": "2026-07-28T13:59:14+00:00"},
                {"event": "first_mouth_motion", "at": "2026-07-28T13:59:15+00:00"},
                {"event": "first_video_rendered", "at": "2026-07-28T13:59:18.100+00:00"},
                {"event": "first_non_silent_audio", "at": "2026-07-28T13:59:18.500+00:00"},
                {"event": "first_mouth_motion", "at": "2026-07-28T13:59:18.700+00:00"},
            ]
        )
    )
    runtime = SimpleNamespace(
        renderer=renderer,
        state={
            "link_test_id": "test-1",
            "link_test_started_at": "2026-07-28T13:59:16+00:00",
            "link_test_input_at": "2026-07-28T13:59:18+00:00",
            "link_test_sent_at": "2026-07-28T13:59:18.001+00:00",
        },
    )

    timeline = build_pipeline_timeline(runtime)

    assert timeline["input_to_send_ms"] == 1.0
    assert timeline["input_to_return_audio_ms"] == 500.0
    assert timeline["return_audio_to_mouth_ms"] == 200.0
    assert timeline["first_non_silent_return_audio_at"].endswith("18.500+00:00")


def test_manager_status_follows_active_session_instead_of_stale_requested_session():
    ended = SimpleNamespace(
        session_id="old",
        state={"status": "ended"},
        _link_diagnostics=SimpleNamespace(status=lambda: {"session_id": "old", "latest": {}}),
    )
    active = SimpleNamespace(
        session_id="new",
        state={"status": "active"},
        _link_diagnostics=SimpleNamespace(
            status=lambda: {
                "session_id": "new",
                "active": True,
                "latest": {"session_id": "new"},
                "history_tail": [],
                "sample_count": 1,
            }
        ),
    )
    manager = SimpleNamespace(sessions={"old": ended, "new": active})

    status = manager_link_status(manager, "old")

    assert status["session_id"] == "new"
    assert status["session_switched"] is True


def test_atomic_report_write_never_leaves_partial_json(tmp_path: Path):
    path = tmp_path / "link.report.json"
    atomic_write_json(path, {"version": 1, "rows": list(range(50))})
    atomic_write_json(path, {"version": 2, "nested": {"ok": True}})

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload == {"version": 2, "nested": {"ok": True}}
    assert not list(tmp_path.glob("*.tmp"))


def test_history_conclusion_matches_aggregate_primary():
    rows = []
    for index in range(4):
        rows.append(
            {
                "elapsed_seconds": index * 2,
                "rtc": {
                    "rtt_ms": 140,
                    "video": {
                        "packet_loss_pct": 20,
                        "jitter_buffer_avg_ms": 200,
                        "decoded_fps": 12,
                    },
                },
                "aliver": {"receive_fps": 12, "render_fps": 12},
                "audio": {"return_audio_buffer_ms": 2200},
                "diagnosis": {
                    "primary_bottleneck": "network",
                    "issues": [
                        {"code": "network", "score": 4},
                        {"code": "audio_buffer", "score": 4},
                    ],
                },
            }
        )

    report = build_history_report(rows)

    assert report["primary_bottleneck"] == "network"
    assert "跨网/WebRTC 网络" in report["conclusion_zh"]
