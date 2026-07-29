from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge import simli_link_diagnostics as v1

LINK_DIAGNOSTIC_VERSION = 2


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _round(value: Any, digits: int = 2) -> float | None:
    number = _number(value)
    return round(number, digits) if number is not None else None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _non_negative_diff_ms(later: Any, earlier: Any) -> float | None:
    left = _parse_iso(later)
    right = _parse_iso(earlier)
    if left is None or right is None:
        return None
    value = (left - right).total_seconds() * 1000.0
    return round(value, 1) if value >= 0 else None


def _events_after(renderer: Any, started_at: Any) -> list[dict[str, Any]]:
    rows = list(getattr(renderer, "_diag_events", ()) or ())
    threshold = _parse_iso(started_at)
    if threshold is None:
        return [dict(row) for row in rows]
    result: list[dict[str, Any]] = []
    for row in rows:
        at = _parse_iso(row.get("at"))
        if at is not None and at >= threshold:
            result.append(dict(row))
    return result


def _first_event(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((row for row in rows if row.get("event") == name), None)


def build_pipeline_timeline(runtime: Any) -> dict[str, Any]:
    state = getattr(runtime, "state", {}) or {}
    renderer = getattr(runtime, "renderer", None)
    test_started_at = state.get("link_test_started_at")
    rows = _events_after(renderer, test_started_at)
    first_video = _first_event(rows, "first_video_rendered")
    first_voice = _first_event(rows, "first_non_silent_audio")
    first_mouth = _first_event(rows, "first_mouth_motion")

    if test_started_at:
        input_at = state.get("link_test_input_at")
        sent_at = state.get("link_test_sent_at")
    else:
        input_at = state.get("first_non_silent_input_at")
        sent_at = state.get("first_audio_sent_at")

    voice_at = (first_voice or {}).get("at")
    mouth_at = (first_mouth or {}).get("at")
    render_at = (first_video or {}).get("at")
    return {
        "test_id": state.get("link_test_id"),
        "test_started_at": test_started_at,
        "capture_started_at": state.get("capture_started_at"),
        "first_non_silent_input_at": input_at,
        "first_audio_sent_at": sent_at,
        "first_non_silent_return_audio_at": voice_at,
        "first_video_rendered_at": render_at,
        "first_mouth_motion_at": mouth_at,
        "input_to_send_ms": _non_negative_diff_ms(sent_at, input_at),
        "input_to_return_audio_ms": _non_negative_diff_ms(voice_at, input_at),
        "input_to_first_render_ms": _non_negative_diff_ms(render_at, input_at),
        "return_audio_to_mouth_ms": _non_negative_diff_ms(mouth_at, voice_at),
    }


def _issue(code: str, label: str, score: int, evidence: list[str]) -> dict[str, Any]:
    return {"code": code, "label_zh": label, "score": score, "evidence": evidence}


def build_link_diagnosis(snapshot: dict[str, Any]) -> dict[str, Any]:
    rtc = snapshot.get("rtc") or {}
    local = snapshot.get("aliver") or {}
    audio = snapshot.get("audio") or {}
    issues: list[dict[str, Any]] = []
    suggestions: list[str] = []

    rtt = _number(rtc.get("rtt_ms"))
    video_rtc = rtc.get("video") or {}
    audio_rtc = rtc.get("audio") or {}
    jitter = max(
        _number(video_rtc.get("jitter_ms"), 0.0) or 0.0,
        _number(audio_rtc.get("jitter_ms"), 0.0) or 0.0,
    )
    loss = max(
        _number(video_rtc.get("packet_loss_pct"), 0.0) or 0.0,
        _number(audio_rtc.get("packet_loss_pct"), 0.0) or 0.0,
    )
    jitter_buffer = max(
        _number(video_rtc.get("jitter_buffer_avg_ms"), 0.0) or 0.0,
        _number(audio_rtc.get("jitter_buffer_avg_ms"), 0.0) or 0.0,
    )
    network_score = 0
    network_evidence: list[str] = []
    if rtt is not None and rtt >= 250:
        network_score += 3
        network_evidence.append(f"LiveKit RTT {rtt:.0f} ms，往返延迟较高")
    elif rtt is not None and rtt >= 150:
        network_score += 1
        network_evidence.append(f"LiveKit RTT {rtt:.0f} ms，网络延迟偏高")
    if jitter >= 60:
        network_score += 3
        network_evidence.append(f"接收抖动约 {jitter:.0f} ms")
    elif jitter >= 30:
        network_score += 1
        network_evidence.append(f"接收抖动约 {jitter:.0f} ms")
    if loss >= 5:
        network_score += 3
        network_evidence.append(f"区间丢包率约 {loss:.2f}%")
    elif loss >= 2:
        network_score += 1
        network_evidence.append(f"区间丢包率约 {loss:.2f}%")
    if jitter_buffer >= 220:
        network_score += 3
        network_evidence.append(f"Jitter Buffer 已达到 {jitter_buffer:.0f} ms")
    elif jitter_buffer >= 100:
        network_score += 1
        network_evidence.append(f"Jitter Buffer 约 {jitter_buffer:.0f} ms")
    route = str(rtc.get("route") or "unknown")
    if route == "TCP":
        network_score += 1
        network_evidence.append("WebRTC 当前走 TCP，抖动时更容易排队")
    if network_score:
        issues.append(_issue("network", "跨网/WebRTC 网络", network_score, network_evidence))
        suggestions.append(
            "对比 Simli/LiveKit 直连、当前代理和其他线路；重点观察丢包、RTT P95、Jitter Buffer 与 UDP/TURN 路径。"
        )

    decoded_fps = _number(video_rtc.get("decoded_fps"))
    if not decoded_fps:
        decoded_fps = _number(video_rtc.get("frames_per_second"))
    receive_fps = _number(local.get("receive_fps"))
    render_fps = _number(local.get("render_fps"))
    queue_size = _integer(local.get("video_queue_size"))
    queue_growth = _integer(local.get("video_queue_growth"))
    render_drops = _integer(local.get("video_render_drops_delta"))
    queue_drops = _integer(local.get("video_queue_drops_delta"))
    lateness = abs(_number(local.get("scheduler_lateness_ms"), 0.0) or 0.0)

    sdk_score = 0
    sdk_evidence: list[str] = []
    if (
        decoded_fps is not None
        and decoded_fps >= 12
        and receive_fps is not None
        and receive_fps < decoded_fps * 0.72
    ):
        sdk_score += 4
        sdk_evidence.append(
            f"LiveKit 区间解码约 {decoded_fps:.1f} FPS，但 ALiver 区间只取到 {receive_fps:.1f} FPS"
        )
    if sdk_score:
        issues.append(_issue("sdk_consumer", "LiveKit→ALiver 帧消费", sdk_score, sdk_evidence))
        suggestions.append("检查 SDK 视频迭代器消费与事件循环，不要用播放速度掩盖帧消费不足。")

    local_score = 0
    local_evidence: list[str] = []
    sustained_renderer_gap = (
        receive_fps is not None
        and receive_fps >= 12
        and render_fps is not None
        and render_fps < receive_fps * 0.72
    )
    corroborated = lateness >= 100 or queue_growth >= 8 or render_drops > 0 or queue_drops > 0
    if sustained_renderer_gap and corroborated:
        local_score += 4
        local_evidence.append(
            f"区间收帧约 {receive_fps:.1f} FPS、渲染 {render_fps:.1f} FPS，并伴随积压/迟到/丢帧"
        )
    if queue_growth >= 15:
        local_score += 2
        local_evidence.append(f"本采样区间视频队列增长 {queue_growth} 帧")
    if queue_size >= 60 and lateness >= 100:
        local_score += 2
        local_evidence.append(f"视频队列积压 {queue_size} 帧且调度迟到 {lateness:.0f} ms")
    if render_drops > 0 or queue_drops > 0:
        local_score += 2
        local_evidence.append(f"本区间本地丢帧：队列 {queue_drops}、调度 {render_drops}")
    if local_score:
        issues.append(_issue("local_renderer", "ALiver 本地渲染", local_score, local_evidence))
        suggestions.append("优化窗口转换/显示并丢弃真正过时的帧；先确认区间 FPS，而不是 arrival burst FPS。")

    return_buffer = _number(audio.get("return_audio_buffer_ms"), 0.0) or 0.0
    waveout_pending = _number(audio.get("waveout_pending_ms"), 0.0) or 0.0
    underflows = _integer(audio.get("underflows_delta"))
    audio_score = 0
    audio_evidence: list[str] = []
    if return_buffer >= 800:
        audio_score += 4
        audio_evidence.append(f"Simli 返回音频缓冲达到 {return_buffer:.0f} ms")
    elif return_buffer >= 400:
        audio_score += 2
        audio_evidence.append(f"Simli 返回音频缓冲约 {return_buffer:.0f} ms")
    if waveout_pending >= 250:
        audio_score += 3
        audio_evidence.append(f"Windows 播放队列积压约 {waveout_pending:.0f} ms")
    if underflows > 0:
        audio_score += 2
        audio_evidence.append(f"本区间发生 {underflows} 次音频欠载")
    if audio_score:
        issues.append(_issue("audio_buffer", "返回音频缓冲/播放", audio_score, audio_evidence))
        suggestions.append("在新一轮讲话前裁掉旧待机媒体，将返回音频队列压到约 350～500 ms，并保留短 waveOut 滚动缓冲。")

    if not issues:
        health = "good" if rtc.get("available") else "insufficient"
        primary = "healthy" if rtc.get("available") else "insufficient"
        conclusion = (
            "当前采样未发现明确链路瓶颈。"
            if rtc.get("available")
            else "尚未取得足够的 LiveKit RTC 数据。"
        )
    else:
        issues.sort(key=lambda row: row["score"], reverse=True)
        primary_issue = issues[0]
        primary = str(primary_issue["code"])
        health = "bad" if int(primary_issue["score"]) >= 4 else "warning"
        conclusion = f"主要问题：{primary_issue['label_zh']}。"
        concurrent = [row["label_zh"] for row in issues[1:] if int(row["score"]) >= 3]
        if concurrent:
            conclusion += f" 同时存在：{'、'.join(concurrent)}。"

    scores = {
        "network": next((row["score"] for row in issues if row["code"] == "network"), 0),
        "sdk_consumer": next(
            (row["score"] for row in issues if row["code"] == "sdk_consumer"), 0
        ),
        "local_renderer": next(
            (row["score"] for row in issues if row["code"] == "local_renderer"), 0
        ),
        "audio_buffer": next(
            (row["score"] for row in issues if row["code"] == "audio_buffer"), 0
        ),
        "upstream": 0,
    }
    evidence = [item for row in issues for item in row["evidence"]]
    labels = {
        "network": "跨网/WebRTC 网络",
        "sdk_consumer": "LiveKit→ALiver 帧消费",
        "local_renderer": "ALiver 本地渲染",
        "audio_buffer": "返回音频缓冲/播放",
        "healthy": "未发现明确瓶颈",
        "insufficient": "数据不足",
    }
    return {
        "health": health,
        "primary_bottleneck": primary,
        "primary_bottleneck_zh": labels.get(primary, primary),
        "scores": scores,
        "issues": issues,
        "conclusion_zh": conclusion,
        "evidence": evidence,
        "suggestions": suggestions,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 2)


def build_history_report(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {"samples": 0, "conclusion_zh": "暂无链路采样数据。"}

    def collect(path: tuple[str, ...]) -> list[float]:
        result: list[float] = []
        for row in history:
            current: Any = row
            for key in path:
                current = current.get(key) if isinstance(current, dict) else None
            number = _number(current)
            if number is not None:
                result.append(number)
        return result

    rtt = collect(("rtc", "rtt_ms"))
    loss = collect(("rtc", "video", "packet_loss_pct"))
    jitter_buffer = collect(("rtc", "video", "jitter_buffer_avg_ms"))
    decoded = collect(("rtc", "video", "decoded_fps"))
    receive = collect(("aliver", "receive_fps"))
    render = collect(("aliver", "render_fps"))
    audio_buffer = collect(("audio", "return_audio_buffer_ms"))
    bottlenecks: dict[str, int] = {}
    issue_counts: dict[str, int] = {}
    for row in history:
        diagnosis = row.get("diagnosis") or {}
        name = str(diagnosis.get("primary_bottleneck") or "unknown")
        bottlenecks[name] = bottlenecks.get(name, 0) + 1
        for issue in diagnosis.get("issues") or []:
            code = str(issue.get("code") or "unknown")
            issue_counts[code] = issue_counts.get(code, 0) + 1
    primary = max(bottlenecks, key=bottlenecks.get)
    labels = {
        "network": "跨网/WebRTC 网络",
        "sdk_consumer": "LiveKit→ALiver 帧消费",
        "local_renderer": "ALiver 本地渲染",
        "audio_buffer": "返回音频缓冲/播放",
        "healthy": "未发现明确瓶颈",
        "insufficient": "数据不足",
    }
    concurrent = [
        labels.get(code, code)
        for code, count in sorted(issue_counts.items(), key=lambda item: item[1], reverse=True)
        if code != primary and count >= max(2, len(history) // 4)
    ]
    conclusion = f"整段测试主要问题：{labels.get(primary, primary)}。"
    if concurrent:
        conclusion += f" 同时频繁出现：{'、'.join(concurrent)}。"
    return {
        "samples": len(history),
        "duration_seconds": round(
            max(
                0.0,
                (_number(history[-1].get("elapsed_seconds"), 0.0) or 0.0)
                - (_number(history[0].get("elapsed_seconds"), 0.0) or 0.0),
            ),
            1,
        ),
        "rtt_ms_avg": round(statistics.fmean(rtt), 2) if rtt else None,
        "rtt_ms_p95": _percentile(rtt, 0.95),
        "rtt_ms_max": round(max(rtt), 2) if rtt else None,
        "packet_loss_pct_median": round(statistics.median(loss), 2) if loss else None,
        "packet_loss_pct_p95": _percentile(loss, 0.95),
        "jitter_buffer_ms_p95": _percentile(jitter_buffer, 0.95),
        "decoded_fps_avg": round(statistics.fmean(decoded), 2) if decoded else None,
        "aliver_receive_fps_avg": round(statistics.fmean(receive), 2) if receive else None,
        "render_fps_avg": round(statistics.fmean(render), 2) if render else None,
        "audio_buffer_ms_avg": round(statistics.fmean(audio_buffer), 2) if audio_buffer else None,
        "audio_buffer_ms_p95": _percentile(audio_buffer, 0.95),
        "primary_bottleneck": primary,
        "bottleneck_counts": bottlenecks,
        "issue_counts": issue_counts,
        "conclusion_zh": conclusion,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _active_runtime(manager: Any, requested_session_id: str | None = None) -> Any | None:
    sessions = getattr(manager, "sessions", {}) or {}
    if requested_session_id:
        requested = sessions.get(requested_session_id)
        if requested is not None and requested.state.get("status") in {"active", "starting"}:
            return requested
    active = [
        runtime
        for runtime in sessions.values()
        if runtime.state.get("status") in {"active", "starting"}
    ]
    return active[-1] if active else None


def _report_runtime(manager: Any, session_id: str | None = None) -> Any | None:
    sessions = getattr(manager, "sessions", {}) or {}
    if session_id and session_id in sessions:
        return sessions[session_id]
    active = _active_runtime(manager)
    if active is not None:
        return active
    return next(reversed(sessions.values()), None) if sessions else None


def install_link_diagnostics_v2(runtime_class: type) -> None:
    monitor_class = v1.SimliLinkMonitor
    if getattr(monitor_class, "_aliver_link_diagnostics_v2", False):
        return

    original_init = monitor_class.__init__

    def patched_init(monitor: Any, runtime: Any) -> None:
        original_init(monitor, runtime)
        monitor._v2_previous_counts = {}
        monitor._v2_test_history_start = None

    def begin_test(monitor: Any) -> dict[str, Any]:
        state = getattr(monitor.runtime, "state", {}) or {}
        test_id = str(uuid.uuid4())
        started_at = _utc_iso()
        state.update(
            {
                "link_test_id": test_id,
                "link_test_started_at": started_at,
                "link_test_input_at": None,
                "link_test_input_dbfs": None,
                "link_test_sent_at": None,
            }
        )
        renderer = getattr(monitor.runtime, "renderer", None)
        if renderer is not None:
            renderer._diag_first_audio_onset = None
            renderer._diag_first_mouth_onset = None
            renderer._diag_first_video_render_clock = None
            baseline = getattr(renderer, "_diag_motion_baseline", None)
            if hasattr(baseline, "clear"):
                baseline.clear()
            record = getattr(renderer, "_diag_record_event", None)
            if callable(record):
                record("link_test_started", test_id=test_id)
        monitor._v2_test_history_start = len(monitor.history)
        return {"session_id": monitor.session_id, "test_id": test_id, "started_at": started_at}

    def local_snapshot(monitor: Any, elapsed: float) -> tuple[dict[str, Any], dict[str, Any]]:
        renderer = getattr(monitor.runtime, "renderer", None)
        renderer_status: dict[str, Any] = {}
        if renderer is not None and callable(getattr(renderer, "status", None)):
            try:
                renderer_status = dict(renderer.status() or {})
            except Exception:
                renderer_status = dict(getattr(renderer, "_metrics", {}) or {})
        stream = getattr(renderer, "_audio_stream", None) if renderer is not None else None
        pending_seconds = _number(getattr(stream, "_pending_seconds", 0.0), 0.0) or 0.0
        current = {
            "received": _integer(renderer_status.get("video_frames_received")),
            "rendered": _integer(renderer_status.get("video_frames_rendered")),
            "queue_drops": _integer(renderer_status.get("video_queue_drops")),
            "render_drops": _integer(renderer_status.get("video_frames_dropped")),
            "underflows": _integer(renderer_status.get("audio_underflows")),
            "queue_size": _integer(renderer_status.get("video_queue_size")),
        }
        previous = dict(monitor._v2_previous_counts or {})
        monitor._v2_previous_counts = current

        def delta(key: str) -> int:
            if not previous:
                return 0
            return max(0, current[key] - _integer(previous.get(key)))

        receive_fps = round(delta("received") / elapsed, 2) if previous and elapsed > 0 else None
        render_fps = round(delta("rendered") / elapsed, 2) if previous and elapsed > 0 else None
        aliver = {
            "receive_fps": receive_fps,
            "render_fps": render_fps,
            "arrival_burst_fps": _round(renderer_status.get("receive_fps"), 2),
            "render_burst_fps": _round(renderer_status.get("render_fps_recent"), 2),
            "source_pts_fps": _round(renderer_status.get("source_pts_fps"), 2),
            "video_playback_speed_ratio": _round(
                renderer_status.get("video_playback_speed_ratio"), 3
            ),
            "video_queue_size": current["queue_size"],
            "video_queue_growth": (
                current["queue_size"] - _integer(previous.get("queue_size")) if previous else 0
            ),
            "video_queue_drops_total": current["queue_drops"],
            "video_queue_drops_delta": delta("queue_drops"),
            "video_render_drops_total": current["render_drops"],
            "video_render_drops_delta": delta("render_drops"),
            "scheduler_lateness_ms": _round(renderer_status.get("scheduler_lateness_ms"), 1),
            "av_offset_ms": _round(renderer_status.get("av_offset_ms"), 1),
            "renderer_status": renderer_status.get("status"),
        }
        runtime_state = getattr(monitor.runtime, "state", {}) or {}
        audio = {
            "gpt_out_dbfs": _round(runtime_state.get("last_input_dbfs"), 1),
            "simli_input_queue_chunks": getattr(
                getattr(monitor.runtime, "audio_queue", None), "qsize", lambda: 0
            )(),
            "simli_sent_chunks": _integer(runtime_state.get("sent_chunks")),
            "simli_dropped_input_chunks": _integer(runtime_state.get("dropped_chunks")),
            "return_audio_buffer_ms": _round(renderer_status.get("audio_buffer_ms"), 1),
            "return_audio_queue_size": _integer(renderer_status.get("audio_queue_size")),
            "waveout_pending_ms": round(pending_seconds * 1000.0, 1),
            "underflows_total": current["underflows"],
            "underflows_delta": delta("underflows"),
            "audio_output_backend": renderer_status.get("audio_output_backend"),
            "audio_output_latency_ms": _round(renderer_status.get("audio_output_latency_ms"), 1),
            "idle_trim_count": _integer(renderer_status.get("idle_trim_count")),
            "idle_trim_audio_ms_total": _round(
                renderer_status.get("idle_trim_audio_ms_total"), 1
            ),
            "idle_trim_video_frames_total": _integer(
                renderer_status.get("idle_trim_video_frames_total")
            ),
            "idle_trim_last_audio_ms": _round(renderer_status.get("idle_trim_last_audio_ms"), 1),
            "idle_trim_post_audio_buffer_ms": _round(
                renderer_status.get("idle_trim_post_audio_buffer_ms"), 1
            ),
        }
        return aliver, audio

    async def sample(monitor: Any) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = (
            now - monitor._last_sample_monotonic
            if monitor._last_sample_monotonic is not None
            else 0.0
        )
        monitor._last_sample_monotonic = now
        rtc = await monitor._rtc_snapshot(elapsed)
        aliver, audio = local_snapshot(monitor, elapsed)
        snapshot = {
            "diagnostic_version": LINK_DIAGNOSTIC_VERSION,
            "session_id": monitor.session_id,
            "at_local": v1.local_iso(),
            "at_utc": v1.utc_iso(),
            "elapsed_seconds": round(now - monitor._started, 3),
            "sample_elapsed_seconds": round(elapsed, 3),
            "runtime_status": (getattr(monitor.runtime, "state", {}) or {}).get("status"),
            "network_policy": (getattr(monitor.runtime, "state", {}) or {}).get(
                "network_policy"
            ),
            "rtc": rtc,
            "aliver": aliver,
            "audio": audio,
            "timeline": build_pipeline_timeline(monitor.runtime),
        }
        snapshot["diagnosis"] = build_link_diagnosis(snapshot)
        monitor.latest = snapshot
        monitor.history.append(snapshot)
        monitor._append_sample(snapshot)
        if len(monitor.history) % 5 == 0:
            monitor._write_report()
        return snapshot

    def status(monitor: Any) -> dict[str, Any]:
        state = getattr(monitor.runtime, "state", {}) or {}
        return {
            "session_id": monitor.session_id,
            "active": bool(monitor._task and not monitor._task.done() and not monitor._stop.is_set()),
            "latest": monitor.latest,
            "history_tail": list(monitor.history)[-12:],
            "sample_count": len(monitor.history),
            "event_log_path": str(monitor.event_path),
            "report_path": str(monitor.report_path),
            "test": {
                "test_id": state.get("link_test_id"),
                "started_at": state.get("link_test_started_at"),
                "input_at": state.get("link_test_input_at"),
                "sent_at": state.get("link_test_sent_at"),
            },
        }

    def report(monitor: Any) -> dict[str, Any]:
        history = list(monitor.history)
        start = monitor._v2_test_history_start
        aggregate_history = history[start:] if isinstance(start, int) else history
        return {
            **status(monitor),
            "aggregate": build_history_report(aggregate_history),
            "history": history[-60:],
        }

    def write_report(monitor: Any) -> None:
        try:
            atomic_write_json(monitor.report_path, report(monitor))
        except OSError:
            pass

    monitor_class.__init__ = patched_init
    monitor_class.begin_test = begin_test
    monitor_class.sample = sample
    monitor_class.status = status
    monitor_class.report = report
    monitor_class._write_report = write_report
    monitor_class._aliver_link_diagnostics_v2 = True
    runtime_class._aliver_link_diagnostics_v2 = True


def manager_link_status(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = _active_runtime(manager, session_id)
    if runtime is None:
        return {
            "session_id": None,
            "requested_session_id": session_id,
            "active": False,
            "latest": None,
            "history_tail": [],
            "sample_count": 0,
            "message_zh": "当前 Bridge 没有运行中的 Simli 会话。",
        }
    monitor = getattr(runtime, "_link_diagnostics", None)
    if monitor is None:
        return {
            "session_id": getattr(runtime, "session_id", None),
            "requested_session_id": session_id,
            "active": False,
            "latest": None,
            "history_tail": [],
            "sample_count": 0,
            "message_zh": "当前会话尚未启动链路诊断采样。",
        }
    result = monitor.status()
    result["requested_session_id"] = session_id
    result["session_switched"] = bool(session_id and session_id != result.get("session_id"))
    return result


def manager_link_report(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = _report_runtime(manager, session_id)
    if runtime is None:
        return manager_link_status(manager, session_id)
    monitor = getattr(runtime, "_link_diagnostics", None)
    if monitor is None:
        return manager_link_status(manager, session_id)
    monitor._write_report()
    return monitor.report()


def manager_begin_link_test(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = _active_runtime(manager, session_id)
    if runtime is None:
        raise ValueError("当前 Bridge 没有运行中的 Simli 会话")
    monitor = getattr(runtime, "_link_diagnostics", None)
    if monitor is None or not callable(getattr(monitor, "begin_test", None)):
        raise ValueError("当前会话尚未启用链路诊断 v2")
    return monitor.begin_test()
