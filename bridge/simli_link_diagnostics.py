from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LINK_DIAGNOSTIC_VERSION = 1
SAMPLE_INTERVAL_SECONDS = 2.0
RTC_TIMEOUT_SECONDS = 1.25
HISTORY_LIMIT = 180
DIAGNOSTICS_DIR = Path(__file__).resolve().parent / "diagnostics" / "link"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def sanitize_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    return cleaned[:100] or "unknown-session"


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


def _protobuf_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    try:
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(message, preserving_proto_field_name=True)
    except Exception:
        return {"raw": str(message)}


def _flatten_stat(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    top_type = "unknown"
    if len(row) == 1:
        first_key, first_value = next(iter(row.items()))
        if isinstance(first_value, dict):
            top_type = str(first_key)
    if "type" in row and not isinstance(row.get("type"), (dict, list)):
        top_type = str(row.get("type") or top_type)
    result["_type"] = top_type

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child)
                elif key not in result:
                    result[key] = child
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(row)
    return result


def normalize_rtc_stats(stats: Any) -> list[dict[str, Any]]:
    rows = list(getattr(stats, "subscriber_stats", []) or [])
    return [_flatten_stat(_protobuf_to_dict(row)) for row in rows]


def _media_kind(row: dict[str, Any]) -> str | None:
    explicit = " ".join(
        str(row.get(key) or "") for key in ("kind", "media_type", "track_kind", "type")
    ).lower()
    if "video" in explicit:
        return "video"
    if "audio" in explicit:
        return "audio"
    if any(key in row for key in ("frames_decoded", "frames_received", "frame_width", "frame_height")):
        return "video"
    if any(key in row for key in ("total_samples_received", "audio_level", "concealed_samples")):
        return "audio"
    return None


def _select_inbound(rows: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        stat_type = str(row.get("_type") or "").lower()
        if "inbound" not in stat_type and not any(
            key in row for key in ("packets_received", "bytes_received", "frames_decoded")
        ):
            continue
        if _media_kind(row) != kind:
            continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda row: _number(row.get("bytes_received"), 0.0) or 0.0)


def _select_candidate_pair(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if "candidate_pair" in str(row.get("_type") or "").lower()
        or "current_round_trip_time" in row
    ]
    if not candidates:
        return None

    def score(row: dict[str, Any]) -> tuple[int, float]:
        state = str(row.get("state") or "").lower()
        selected = int(bool(row.get("selected") or row.get("nominated")))
        if "succeeded" in state or "connected" in state:
            selected += 1
        return selected, _number(row.get("bytes_received"), 0.0) or 0.0

    return max(candidates, key=score)


def _candidate_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        if "candidate" not in str(row.get("_type") or "").lower():
            continue
        identifier = str(row.get("id") or row.get("candidate_id") or "")
        if identifier:
            values[identifier] = row
    return values


def _jitter_buffer_average_ms(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    delay = _number(row.get("jitter_buffer_delay"))
    emitted = _number(row.get("jitter_buffer_emitted_count"))
    if delay is None or emitted is None or emitted <= 0:
        return None
    return round(delay / emitted * 1000.0, 2)


def _media_totals(row: dict[str, Any] | None) -> dict[str, float]:
    row = row or {}
    return {
        "bytes_received": _number(row.get("bytes_received"), 0.0) or 0.0,
        "packets_received": _number(row.get("packets_received"), 0.0) or 0.0,
        "packets_lost": _number(row.get("packets_lost"), 0.0) or 0.0,
        "frames_decoded": _number(row.get("frames_decoded"), 0.0) or 0.0,
        "frames_dropped": _number(row.get("frames_dropped"), 0.0) or 0.0,
        "concealed_samples": _number(row.get("concealed_samples"), 0.0) or 0.0,
        "total_samples_received": _number(row.get("total_samples_received"), 0.0) or 0.0,
    }


def _interval_media(
    current: dict[str, float],
    previous: dict[str, float] | None,
    elapsed: float,
) -> dict[str, float | None]:
    if not previous or elapsed <= 0:
        return {
            "bitrate_kbps": None,
            "packet_loss_pct": None,
            "decoded_fps": None,
            "dropped_fps": None,
            "concealment_pct": None,
        }

    def delta(key: str) -> float:
        return max(0.0, current[key] - previous.get(key, 0.0))

    received = delta("packets_received")
    lost = delta("packets_lost")
    samples = delta("total_samples_received")
    concealed = delta("concealed_samples")
    total_packets = received + lost
    return {
        "bitrate_kbps": round(delta("bytes_received") * 8.0 / elapsed / 1000.0, 2),
        "packet_loss_pct": round(lost / total_packets * 100.0, 3) if total_packets > 0 else 0.0,
        "decoded_fps": round(delta("frames_decoded") / elapsed, 2),
        "dropped_fps": round(delta("frames_dropped") / elapsed, 2),
        "concealment_pct": round(concealed / samples * 100.0, 3) if samples > 0 else 0.0,
    }


def summarize_rtc_rows(
    rows: list[dict[str, Any]],
    *,
    previous: dict[str, dict[str, float]] | None = None,
    elapsed: float = 0.0,
) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    previous = previous or {}
    video_row = _select_inbound(rows, "video")
    audio_row = _select_inbound(rows, "audio")
    video_totals = _media_totals(video_row)
    audio_totals = _media_totals(audio_row)
    video_interval = _interval_media(video_totals, previous.get("video"), elapsed)
    audio_interval = _interval_media(audio_totals, previous.get("audio"), elapsed)

    pair = _select_candidate_pair(rows)
    candidates = _candidate_map(rows)
    local_candidate = candidates.get(str((pair or {}).get("local_candidate_id") or ""), {})
    remote_candidate = candidates.get(str((pair or {}).get("remote_candidate_id") or ""), {})
    candidate_rows = [row for row in (local_candidate, remote_candidate) if row]
    candidate_types = [str(row.get("candidate_type") or "").lower() for row in candidate_rows]
    protocols = [str(row.get("protocol") or "").lower() for row in candidate_rows]

    if "relay" in candidate_types:
        route = "TURN"
    elif any("tcp" in value for value in protocols):
        route = "TCP"
    elif any("udp" in value for value in protocols):
        route = "UDP"
    else:
        route = "unknown"

    rtt_seconds = _number((pair or {}).get("current_round_trip_time"))
    if rtt_seconds is None:
        rtt_seconds = _number((pair or {}).get("round_trip_time"))

    def media(row: dict[str, Any] | None, interval: dict[str, Any]) -> dict[str, Any]:
        row = row or {}
        jitter = _number(row.get("jitter"))
        return {
            **interval,
            "jitter_ms": round(jitter * 1000.0, 2) if jitter is not None else None,
            "jitter_buffer_avg_ms": _jitter_buffer_average_ms(row),
            "frames_per_second": _round(row.get("frames_per_second"), 2),
            "frames_decoded": _integer(row.get("frames_decoded")),
            "frames_dropped": _integer(row.get("frames_dropped")),
            "packets_received": _integer(row.get("packets_received")),
            "packets_lost": _integer(row.get("packets_lost")),
            "bytes_received": _integer(row.get("bytes_received")),
            "frame_width": _integer(row.get("frame_width")),
            "frame_height": _integer(row.get("frame_height")),
            "freeze_count": _integer(row.get("freeze_count")),
            "total_freeze_duration_seconds": _round(row.get("total_freeze_duration"), 3),
            "concealed_samples": _integer(row.get("concealed_samples")),
        }

    summary = {
        "available": bool(video_row or audio_row or pair),
        "rtt_ms": round(rtt_seconds * 1000.0, 2) if rtt_seconds is not None else None,
        "available_incoming_kbps": (
            round((_number((pair or {}).get("available_incoming_bitrate")) or 0.0) / 1000.0, 2)
            if _number((pair or {}).get("available_incoming_bitrate")) is not None
            else None
        ),
        "route": route,
        "protocols": sorted({value for value in protocols if value}),
        "candidate_types": sorted({value for value in candidate_types if value}),
        "candidate_pair_state": str((pair or {}).get("state") or "unknown"),
        "video": media(video_row, video_interval),
        "audio": media(audio_row, audio_interval),
        "raw_stat_types": sorted({str(row.get("_type") or "unknown") for row in rows}),
    }
    return summary, {"video": video_totals, "audio": audio_totals}


def _first_event(events: Any, name: str) -> dict[str, Any] | None:
    try:
        rows = list(events)
    except TypeError:
        return None
    return next((dict(row) for row in rows if row.get("event") == name), None)


def _iso_diff_ms(later: Any, earlier: Any) -> float | None:
    if not later or not earlier:
        return None
    try:
        left = datetime.fromisoformat(str(later).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
        return round((left - right).total_seconds() * 1000.0, 1)
    except (TypeError, ValueError):
        return None


def build_pipeline_timeline(runtime: Any) -> dict[str, Any]:
    renderer = getattr(runtime, "renderer", None)
    events = getattr(renderer, "_diag_events", ()) if renderer is not None else ()
    first_video = _first_event(events, "first_video_received")
    first_playback = _first_event(events, "audio_playback_started")
    first_voice = _first_event(events, "first_non_silent_audio")
    first_render = _first_event(events, "first_video_rendered")
    first_mouth = _first_event(events, "first_mouth_motion")
    state = getattr(runtime, "state", {}) or {}
    input_at = state.get("first_non_silent_input_at")
    sent_at = state.get("first_audio_sent_at")
    return {
        "capture_started_at": state.get("capture_started_at"),
        "first_non_silent_input_at": input_at,
        "first_audio_sent_at": sent_at,
        "first_video_received_at": (first_video or {}).get("at"),
        "audio_playback_started_at": (first_playback or {}).get("at"),
        "first_non_silent_return_audio_at": (first_voice or {}).get("at"),
        "first_video_rendered_at": (first_render or {}).get("at"),
        "first_mouth_motion_at": (first_mouth or {}).get("at"),
        "input_to_send_ms": _iso_diff_ms(sent_at, input_at),
        "input_to_return_audio_ms": _iso_diff_ms((first_voice or {}).get("at"), input_at),
        "input_to_first_render_ms": _iso_diff_ms((first_render or {}).get("at"), input_at),
        "return_audio_to_mouth_ms": _iso_diff_ms(
            (first_mouth or {}).get("at"), (first_voice or {}).get("at")
        ),
    }


def build_link_diagnosis(snapshot: dict[str, Any]) -> dict[str, Any]:
    rtc = snapshot.get("rtc") or {}
    local = snapshot.get("aliver") or {}
    audio = snapshot.get("audio") or {}
    evidence: list[str] = []
    suggestions: list[str] = []
    scores = {"network": 0, "sdk_consumer": 0, "local_renderer": 0, "audio_buffer": 0, "upstream": 0}

    rtt = _number(rtc.get("rtt_ms"))
    jitter = max(
        _number((rtc.get("video") or {}).get("jitter_ms"), 0.0) or 0.0,
        _number((rtc.get("audio") or {}).get("jitter_ms"), 0.0) or 0.0,
    )
    loss = max(
        _number((rtc.get("video") or {}).get("packet_loss_pct"), 0.0) or 0.0,
        _number((rtc.get("audio") or {}).get("packet_loss_pct"), 0.0) or 0.0,
    )
    if rtt is not None and rtt >= 250:
        scores["network"] += 3
        evidence.append(f"LiveKit RTT {rtt:.0f} ms，网络往返延迟较高")
    elif rtt is not None and rtt >= 150:
        scores["network"] += 1
        evidence.append(f"LiveKit RTT {rtt:.0f} ms，网络延迟偏高")
    if jitter >= 60:
        scores["network"] += 3
        evidence.append(f"接收抖动约 {jitter:.0f} ms，实时媒体到达不稳定")
    elif jitter >= 30:
        scores["network"] += 1
        evidence.append(f"接收抖动约 {jitter:.0f} ms，存在一定网络波动")
    if loss >= 5:
        scores["network"] += 3
        evidence.append(f"区间丢包率约 {loss:.2f}%")
    elif loss >= 2:
        scores["network"] += 1
        evidence.append(f"区间丢包率约 {loss:.2f}%，略高")
    route = str(rtc.get("route") or "unknown")
    if route == "TCP":
        scores["network"] += 1
        evidence.append("WebRTC 当前走 TCP，网络抖动时更容易产生排队延迟")
    elif route == "TURN":
        evidence.append("WebRTC 当前经 TURN 中继")

    decoded_fps = _number((rtc.get("video") or {}).get("decoded_fps"))
    if not decoded_fps:
        decoded_fps = _number((rtc.get("video") or {}).get("frames_per_second"))
    receive_fps = _number(local.get("receive_fps"))
    render_fps = _number(local.get("render_fps"))
    video_queue = _integer(local.get("video_queue_size"))
    video_queue_drops = _integer(local.get("video_queue_drops_delta"))
    render_drops = _integer(local.get("video_render_drops_delta"))

    if decoded_fps and decoded_fps >= 18 and receive_fps is not None and receive_fps < decoded_fps * 0.72:
        scores["sdk_consumer"] += 4
        evidence.append(
            f"LiveKit 解码约 {decoded_fps:.1f} FPS，但 ALiver 只取到 {receive_fps:.1f} FPS，SDK→ALiver 消费跟不上"
        )
    if receive_fps and receive_fps >= 18 and render_fps is not None and render_fps < receive_fps * 0.72:
        scores["local_renderer"] += 4
        evidence.append(
            f"ALiver 收到约 {receive_fps:.1f} FPS，但窗口只渲染 {render_fps:.1f} FPS，本地显示链路为瓶颈"
        )
    if video_queue >= 60:
        scores["local_renderer"] += 3
        evidence.append(f"ALiver 视频队列积压 {video_queue} 帧")
    elif video_queue >= 20:
        scores["local_renderer"] += 1
        evidence.append(f"ALiver 视频队列已有 {video_queue} 帧积压")
    if video_queue_drops > 0 or render_drops > 0:
        scores["local_renderer"] += 2
        evidence.append(
            f"本采样区间发生本地丢帧：队列 {video_queue_drops} 帧、调度 {render_drops} 帧"
        )

    audio_buffer = _number(audio.get("return_audio_buffer_ms"), 0.0) or 0.0
    waveout_pending = _number(audio.get("waveout_pending_ms"), 0.0) or 0.0
    underflows = _integer(audio.get("underflows_delta"))
    if audio_buffer >= 800:
        scores["audio_buffer"] += 4
        evidence.append(f"Simli 返回音频缓冲达到 {audio_buffer:.0f} ms，声音会明显延后")
    elif audio_buffer >= 400:
        scores["audio_buffer"] += 2
        evidence.append(f"Simli 返回音频缓冲约 {audio_buffer:.0f} ms，延迟偏大")
    if waveout_pending >= 250:
        scores["audio_buffer"] += 3
        evidence.append(f"Windows 播放队列积压约 {waveout_pending:.0f} ms")
    if underflows > 0:
        scores["audio_buffer"] += 2
        evidence.append(f"本采样区间发生 {underflows} 次音频欠载，可能产生卡带/断续声")

    if (
        decoded_fps is not None
        and decoded_fps < 15
        and scores["network"] == 0
        and video_queue < 10
        and scores["local_renderer"] == 0
    ):
        scores["upstream"] += 2
        evidence.append(f"网络指标未见明显异常，但 LiveKit 解码视频仅约 {decoded_fps:.1f} FPS")

    primary = max(scores, key=scores.get)
    maximum = scores[primary]
    labels = {
        "network": "跨网/WebRTC 网络",
        "sdk_consumer": "LiveKit→ALiver 帧消费",
        "local_renderer": "ALiver 本地渲染",
        "audio_buffer": "返回音频缓冲/播放",
        "upstream": "Simli 上游输出",
    }
    if maximum <= 0:
        health = "good" if rtc.get("available") else "insufficient"
        primary = "healthy" if rtc.get("available") else "insufficient"
        conclusion = (
            "当前采样未发现明确链路瓶颈。"
            if rtc.get("available")
            else "尚未取得足够的 LiveKit RTC 数据，暂时无法判断网络与本地瓶颈。"
        )
    else:
        health = "bad" if maximum >= 4 else "warning"
        conclusion = f"当前最可能的瓶颈：{labels[primary]}。"

    if scores["network"]:
        suggestions.append("对比关闭/更换 VPN、代理或网络线路后重新采样；重点观察 RTT、Jitter、丢包率和 UDP/TCP 路径。")
    if scores["sdk_consumer"]:
        suggestions.append("优先优化 LiveKit 视频流消费，避免接收队列积压；不要先用 playback_speed 掩盖帧消费不足。")
    if scores["local_renderer"]:
        suggestions.append("降低 OpenCV 窗口处理负载并及时丢弃过期帧，让渲染 FPS 跟上实际接收 FPS。")
    if scores["audio_buffer"]:
        suggestions.append("压低返回音频和 waveOut 排队时长，避免用大缓冲换取表面连续性。")
    if scores["upstream"]:
        suggestions.append("若多次测试网络正常但 LiveKit 解码 FPS 仍偏低，再对比 Simli 的其他模型/transport 或供应商。")

    return {
        "health": health,
        "primary_bottleneck": primary,
        "primary_bottleneck_zh": labels.get(primary, "未发现明确瓶颈"),
        "scores": scores,
        "conclusion_zh": conclusion,
        "evidence": evidence,
        "suggestions": suggestions,
    }


def _average(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def build_history_report(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {"samples": 0, "conclusion_zh": "暂无链路采样数据。"}

    def collect(path: tuple[str, ...]) -> list[float]:
        values: list[float] = []
        for row in history:
            current: Any = row
            for key in path:
                current = current.get(key) if isinstance(current, dict) else None
            number = _number(current)
            if number is not None:
                values.append(number)
        return values

    rtt = collect(("rtc", "rtt_ms"))
    jitter = collect(("rtc", "video", "jitter_ms"))
    loss = collect(("rtc", "video", "packet_loss_pct"))
    decoded = collect(("rtc", "video", "decoded_fps"))
    receive = collect(("aliver", "receive_fps"))
    render = collect(("aliver", "render_fps"))
    audio_buffer = collect(("audio", "return_audio_buffer_ms"))
    bottlenecks: dict[str, int] = {}
    for row in history:
        name = str((row.get("diagnosis") or {}).get("primary_bottleneck") or "unknown")
        bottlenecks[name] = bottlenecks.get(name, 0) + 1
    primary = max(bottlenecks, key=bottlenecks.get)
    return {
        "samples": len(history),
        "duration_seconds": round(
            max(0.0, (_number(history[-1].get("elapsed_seconds"), 0.0) or 0.0) - (_number(history[0].get("elapsed_seconds"), 0.0) or 0.0)),
            1,
        ),
        "rtt_ms_avg": _average(rtt),
        "rtt_ms_max": round(max(rtt), 2) if rtt else None,
        "jitter_ms_avg": _average(jitter),
        "packet_loss_pct_avg": _average(loss),
        "decoded_fps_avg": _average(decoded),
        "aliver_receive_fps_avg": _average(receive),
        "render_fps_avg": _average(render),
        "audio_buffer_ms_avg": _average(audio_buffer),
        "primary_bottleneck": primary,
        "bottleneck_counts": bottlenecks,
        "conclusion_zh": (history[-1].get("diagnosis") or {}).get("conclusion_zh"),
    }


class SimliLinkMonitor:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.session_id = str(getattr(runtime, "session_id", "unknown-session"))
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_LIMIT)
        self.latest: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._started = time.monotonic()
        self._last_sample_monotonic: float | None = None
        self._previous_media: dict[str, dict[str, float]] = {}
        self._previous_local: dict[str, int] = {}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"link-{sanitize_filename(self.session_id)}-{stamp}"
        self.event_path = DIAGNOSTICS_DIR / f"{stem}.jsonl"
        self.report_path = DIAGNOSTICS_DIR / f"{stem}.report.json"

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"simli-link-{self.session_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._write_report()

    def _livekit_room(self) -> Any | None:
        client = getattr(self.runtime, "client", None)
        connection = getattr(client, "Connection", None)
        return getattr(connection, "LocalPeer", None)

    def _livekit_host(self) -> str | None:
        client = getattr(self.runtime, "client", None)
        connection = getattr(client, "Connection", None)
        join_info = getattr(connection, "sessionJoinInfo", {}) or {}
        value = str(join_info.get("livekit_url") or "")
        return urlparse(value).hostname if value else None

    async def _rtc_snapshot(self, elapsed: float) -> dict[str, Any]:
        room = self._livekit_room()
        if room is None or not callable(getattr(room, "get_rtc_stats", None)):
            return {
                "available": False,
                "reason": "当前不是可读取 RTC Stats 的 LiveKit 会话",
                "livekit_host": self._livekit_host(),
            }
        try:
            stats = await asyncio.wait_for(room.get_rtc_stats(), timeout=RTC_TIMEOUT_SECONDS)
            rows = normalize_rtc_stats(stats)
            summary, totals = summarize_rtc_rows(
                rows,
                previous=self._previous_media,
                elapsed=elapsed,
            )
            self._previous_media = totals
            summary["livekit_host"] = self._livekit_host()
            state = getattr(room, "connection_state", None)
            summary["connection_state"] = getattr(state, "name", str(state) if state is not None else None)
            return summary
        except TimeoutError:
            return {
                "available": False,
                "reason": f"读取 LiveKit RTC Stats 超过 {RTC_TIMEOUT_SECONDS:.2f} 秒",
                "livekit_host": self._livekit_host(),
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "livekit_host": self._livekit_host(),
            }

    def _local_snapshot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        renderer = getattr(self.runtime, "renderer", None)
        renderer_status: dict[str, Any] = {}
        if renderer is not None and callable(getattr(renderer, "status", None)):
            try:
                renderer_status = dict(renderer.status() or {})
            except Exception:
                renderer_status = dict(getattr(renderer, "_metrics", {}) or {})
        stream = getattr(renderer, "_audio_stream", None) if renderer is not None else None
        pending_seconds = _number(getattr(stream, "_pending_seconds", 0.0), 0.0) or 0.0
        current_local = {
            "video_queue_drops": _integer(renderer_status.get("video_queue_drops")),
            "video_frames_dropped": _integer(renderer_status.get("video_frames_dropped")),
            "audio_underflows": _integer(renderer_status.get("audio_underflows")),
        }
        previous = self._previous_local
        self._previous_local = current_local

        def delta(key: str) -> int:
            if not previous:
                return 0
            return max(0, current_local[key] - previous.get(key, 0))

        aliver = {
            "receive_fps": _round(renderer_status.get("receive_fps"), 2),
            "render_fps": _round(
                renderer_status.get("render_fps_recent") or renderer_status.get("render_fps"), 2
            ),
            "source_pts_fps": _round(renderer_status.get("source_pts_fps"), 2),
            "video_playback_speed_ratio": _round(renderer_status.get("video_playback_speed_ratio"), 3),
            "video_queue_size": _integer(renderer_status.get("video_queue_size")),
            "video_queue_drops_total": current_local["video_queue_drops"],
            "video_queue_drops_delta": delta("video_queue_drops"),
            "video_render_drops_total": current_local["video_frames_dropped"],
            "video_render_drops_delta": delta("video_frames_dropped"),
            "scheduler_lateness_ms": _round(renderer_status.get("scheduler_lateness_ms"), 1),
            "av_offset_ms": _round(renderer_status.get("av_offset_ms"), 1),
            "renderer_status": renderer_status.get("status"),
        }
        runtime_state = getattr(self.runtime, "state", {}) or {}
        audio = {
            "gpt_out_dbfs": _round(runtime_state.get("last_input_dbfs"), 1),
            "simli_input_queue_chunks": getattr(getattr(self.runtime, "audio_queue", None), "qsize", lambda: 0)(),
            "simli_sent_chunks": _integer(runtime_state.get("sent_chunks")),
            "simli_dropped_input_chunks": _integer(runtime_state.get("dropped_chunks")),
            "return_audio_buffer_ms": _round(renderer_status.get("audio_buffer_ms"), 1),
            "return_audio_queue_size": _integer(renderer_status.get("audio_queue_size")),
            "waveout_pending_ms": round(pending_seconds * 1000.0, 1),
            "underflows_total": current_local["audio_underflows"],
            "underflows_delta": delta("audio_underflows"),
            "audio_output_backend": renderer_status.get("audio_output_backend"),
            "audio_output_latency_ms": _round(renderer_status.get("audio_output_latency_ms"), 1),
        }
        return aliver, audio

    async def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = now - self._last_sample_monotonic if self._last_sample_monotonic is not None else 0.0
        self._last_sample_monotonic = now
        rtc = await self._rtc_snapshot(elapsed)
        aliver, audio = self._local_snapshot()
        snapshot = {
            "diagnostic_version": LINK_DIAGNOSTIC_VERSION,
            "session_id": self.session_id,
            "at_local": local_iso(),
            "at_utc": utc_iso(),
            "elapsed_seconds": round(now - self._started, 3),
            "runtime_status": (getattr(self.runtime, "state", {}) or {}).get("status"),
            "rtc": rtc,
            "aliver": aliver,
            "audio": audio,
            "timeline": build_pipeline_timeline(self.runtime),
        }
        snapshot["diagnosis"] = build_link_diagnosis(snapshot)
        self.latest = snapshot
        self.history.append(snapshot)
        self._append_sample(snapshot)
        if len(self.history) % 5 == 0:
            self._write_report()
        return snapshot

    def _append_sample(self, snapshot: dict[str, Any]) -> None:
        try:
            DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
            with self.event_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(snapshot, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _write_report(self) -> None:
        report = self.report()
        try:
            DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "active": bool(self._task and not self._task.done() and not self._stop.is_set()),
            "latest": self.latest,
            "history_tail": list(self.history)[-12:],
            "sample_count": len(self.history),
            "event_log_path": str(self.event_path),
            "report_path": str(self.report_path),
        }

    def report(self) -> dict[str, Any]:
        history = list(self.history)
        return {
            **self.status(),
            "aggregate": build_history_report(history),
            "history": history[-60:],
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            state = getattr(self.runtime, "state", {}) or {}
            if state.get("status") not in {"active", "starting"}:
                break
            try:
                await self.sample()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=SAMPLE_INTERVAL_SECONDS)
            except TimeoutError:
                continue
        self._write_report()


def install_link_diagnostics(runtime_class: type) -> None:
    if getattr(runtime_class, "_aliver_link_diagnostics_v1", False):
        return
    original_start = runtime_class.start
    original_stop = runtime_class.stop

    async def patched_start(runtime: Any) -> dict[str, Any]:
        result = await original_start(runtime)
        if getattr(runtime, "_link_diagnostics", None) is None:
            monitor = SimliLinkMonitor(runtime)
            runtime._link_diagnostics = monitor
            monitor.start()
        return result

    async def patched_stop(runtime: Any) -> dict[str, Any]:
        monitor = getattr(runtime, "_link_diagnostics", None)
        if monitor is not None:
            await monitor.stop()
        return await original_stop(runtime)

    runtime_class.start = patched_start
    runtime_class.stop = patched_stop
    runtime_class._aliver_link_diagnostics_v1 = True


def _find_runtime(manager: Any, session_id: str | None = None) -> Any | None:
    sessions = getattr(manager, "sessions", {}) or {}
    if session_id:
        return sessions.get(session_id)
    active = [runtime for runtime in sessions.values() if runtime.state.get("status") == "active"]
    if active:
        return active[-1]
    return next(reversed(sessions.values()), None) if sessions else None


def manager_link_status(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = _find_runtime(manager, session_id)
    if runtime is None:
        return {
            "session_id": session_id,
            "active": False,
            "latest": None,
            "history_tail": [],
            "sample_count": 0,
            "message_zh": "当前 Bridge 没有 Simli 会话。",
        }
    monitor = getattr(runtime, "_link_diagnostics", None)
    if monitor is None:
        return {
            "session_id": getattr(runtime, "session_id", session_id),
            "active": False,
            "latest": None,
            "history_tail": [],
            "sample_count": 0,
            "message_zh": "当前会话尚未启动链路诊断采样。",
        }
    return monitor.status()


def manager_link_report(manager: Any, session_id: str | None = None) -> dict[str, Any]:
    runtime = _find_runtime(manager, session_id)
    if runtime is None:
        return manager_link_status(manager, session_id)
    monitor = getattr(runtime, "_link_diagnostics", None)
    if monitor is None:
        return manager_link_status(manager, session_id)
    monitor._write_report()
    return monitor.report()
