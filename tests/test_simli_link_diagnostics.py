from bridge.simli_link_diagnostics import (
    build_history_report,
    build_link_diagnosis,
    summarize_rtc_rows,
)


def sample_rows():
    return [
        {
            "_type": "inbound_rtp",
            "kind": "video",
            "bytes_received": 1_500_000,
            "packets_received": 1500,
            "packets_lost": 15,
            "frames_decoded": 500,
            "frames_dropped": 4,
            "frames_per_second": 25,
            "jitter": 0.018,
            "jitter_buffer_delay": 12.0,
            "jitter_buffer_emitted_count": 500,
            "frame_width": 512,
            "frame_height": 512,
        },
        {
            "_type": "inbound_rtp",
            "kind": "audio",
            "bytes_received": 320_000,
            "packets_received": 950,
            "packets_lost": 5,
            "jitter": 0.012,
            "jitter_buffer_delay": 8.0,
            "jitter_buffer_emitted_count": 1000,
            "total_samples_received": 480_000,
            "concealed_samples": 1200,
        },
        {
            "_type": "candidate_pair",
            "id": "pair-1",
            "state": "succeeded",
            "selected": True,
            "current_round_trip_time": 0.18,
            "available_incoming_bitrate": 4_500_000,
            "bytes_received": 2_000_000,
            "local_candidate_id": "local-1",
            "remote_candidate_id": "remote-1",
        },
        {
            "_type": "local_candidate",
            "id": "local-1",
            "candidate_type": "host",
            "protocol": "udp",
        },
        {
            "_type": "remote_candidate",
            "id": "remote-1",
            "candidate_type": "srflx",
            "protocol": "udp",
        },
    ]


def test_rtc_summary_calculates_interval_metrics_and_route():
    previous = {
        "video": {
            "bytes_received": 1_000_000,
            "packets_received": 1000,
            "packets_lost": 10,
            "frames_decoded": 450,
            "frames_dropped": 2,
            "concealed_samples": 0,
            "total_samples_received": 0,
        },
        "audio": {
            "bytes_received": 280_000,
            "packets_received": 900,
            "packets_lost": 4,
            "frames_decoded": 0,
            "frames_dropped": 0,
            "concealed_samples": 1000,
            "total_samples_received": 384_000,
        },
    }

    summary, totals = summarize_rtc_rows(sample_rows(), previous=previous, elapsed=2.0)

    assert summary["available"] is True
    assert summary["route"] == "UDP"
    assert summary["rtt_ms"] == 180.0
    assert summary["video"]["decoded_fps"] == 25.0
    assert summary["video"]["bitrate_kbps"] == 2000.0
    assert summary["video"]["packet_loss_pct"] > 0
    assert summary["video"]["jitter_ms"] == 18.0
    assert summary["video"]["jitter_buffer_avg_ms"] == 24.0
    assert totals["video"]["frames_decoded"] == 500


def test_link_diagnosis_separates_network_from_local_renderer():
    snapshot = {
        "rtc": {
            "available": True,
            "rtt_ms": 55,
            "route": "UDP",
            "video": {"jitter_ms": 8, "packet_loss_pct": 0.2, "decoded_fps": 25},
            "audio": {"jitter_ms": 6, "packet_loss_pct": 0.1},
        },
        "aliver": {
            "receive_fps": 24.5,
            "render_fps": 11.8,
            "video_queue_size": 88,
            "video_queue_drops_delta": 14,
            "video_render_drops_delta": 7,
        },
        "audio": {
            "return_audio_buffer_ms": 160,
            "waveout_pending_ms": 90,
            "underflows_delta": 0,
        },
    }

    diagnosis = build_link_diagnosis(snapshot)

    assert diagnosis["primary_bottleneck"] == "local_renderer"
    assert diagnosis["scores"]["network"] == 0
    assert diagnosis["scores"]["local_renderer"] >= 4
    assert any("渲染" in item or "视频队列" in item for item in diagnosis["evidence"])


def test_link_diagnosis_detects_bad_network():
    snapshot = {
        "rtc": {
            "available": True,
            "rtt_ms": 340,
            "route": "TCP",
            "video": {"jitter_ms": 85, "packet_loss_pct": 7.2, "decoded_fps": 20},
            "audio": {"jitter_ms": 70, "packet_loss_pct": 5.1},
        },
        "aliver": {"receive_fps": 19, "render_fps": 19, "video_queue_size": 2},
        "audio": {"return_audio_buffer_ms": 100, "waveout_pending_ms": 80},
    }

    diagnosis = build_link_diagnosis(snapshot)

    assert diagnosis["primary_bottleneck"] == "network"
    assert diagnosis["health"] == "bad"
    assert diagnosis["scores"]["network"] >= 7


def test_link_diagnosis_detects_audio_backlog():
    snapshot = {
        "rtc": {
            "available": True,
            "rtt_ms": 60,
            "route": "UDP",
            "video": {"jitter_ms": 5, "packet_loss_pct": 0, "decoded_fps": 25},
            "audio": {"jitter_ms": 5, "packet_loss_pct": 0},
        },
        "aliver": {"receive_fps": 25, "render_fps": 24, "video_queue_size": 3},
        "audio": {
            "return_audio_buffer_ms": 1300,
            "waveout_pending_ms": 330,
            "underflows_delta": 2,
        },
    }

    diagnosis = build_link_diagnosis(snapshot)

    assert diagnosis["primary_bottleneck"] == "audio_buffer"
    assert diagnosis["scores"]["audio_buffer"] >= 7


def test_history_report_aggregates_samples():
    rows = []
    for index in range(3):
        rows.append(
            {
                "elapsed_seconds": index * 2,
                "rtc": {
                    "rtt_ms": 50 + index * 10,
                    "video": {"jitter_ms": 8 + index, "packet_loss_pct": 0.2, "decoded_fps": 25},
                },
                "aliver": {"receive_fps": 24, "render_fps": 12},
                "audio": {"return_audio_buffer_ms": 200},
                "diagnosis": {
                    "primary_bottleneck": "local_renderer",
                    "conclusion_zh": "当前最可能的瓶颈：ALiver 本地渲染。",
                },
            }
        )

    report = build_history_report(rows)

    assert report["samples"] == 3
    assert report["duration_seconds"] == 4.0
    assert report["rtt_ms_avg"] == 60.0
    assert report["primary_bottleneck"] == "local_renderer"
