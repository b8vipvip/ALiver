from __future__ import annotations

import asyncio
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from bridge import full_validation as legacy

VALIDATION_DIR = Path(__file__).resolve().parent / "logs" / "full_validation"
LEVELS = {"passed", "warning", "failed", "waiting", "skipped"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def validation_step(
    name: str,
    *,
    phase: str,
    level: str,
    message: str,
    data: Any = None,
    error: str | None = None,
    elapsed_ms: float = 0.0,
) -> dict[str, Any]:
    normalized = level if level in LEVELS else "failed"
    return {
        "name": name,
        "phase": phase,
        "level": normalized,
        "ok": normalized in {"passed", "warning", "skipped"},
        "status": normalized,
        "message": message,
        "data": data,
        "error": error,
        "elapsed_ms": round(float(elapsed_ms), 1),
        "finished_at": _utc_now(),
    }


async def _async_step(
    name: str,
    phase: str,
    callback: Callable[[], Awaitable[Any]],
    classify: Callable[[Any], tuple[str, str]],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        data = await callback()
        level, message = classify(data)
        return validation_step(
            name,
            phase=phase,
            level=level,
            message=message,
            data=data,
            elapsed_ms=(time.monotonic() - started) * 1000,
        )
    except Exception as exc:  # noqa: BLE001 - validation must continue
        return validation_step(
            name,
            phase=phase,
            level="failed",
            message="检查执行失败",
            error=_error(exc),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )


async def _sync_step(
    name: str,
    phase: str,
    callback: Callable[[], Any],
    classify: Callable[[Any], tuple[str, str]],
) -> dict[str, Any]:
    return await _async_step(name, phase, lambda: asyncio.to_thread(callback), classify)


def summarize_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: 0 for key in ("passed", "warning", "failed", "waiting", "skipped")}
    for item in steps:
        level = str(item.get("level") or item.get("status") or "failed")
        counts[level if level in counts else "failed"] += 1
    if counts["failed"]:
        overall = "failed"
    elif counts["waiting"] or counts["warning"]:
        overall = "warning"
    elif counts["passed"]:
        overall = "passed"
    else:
        overall = "skipped"
    return {
        **counts,
        "total": len(steps),
        "overall": overall,
        # compatibility fields used by the old result renderer
        "failed_count": counts["failed"],
        "passed_count": counts["passed"],
    }


def classify_capture_source(preview: dict[str, Any]) -> tuple[str, str]:
    source = str(preview.get("capture_source") or "").lower()
    if source == "windows_graphics_capture":
        return "passed", "已从准确直播伴侣 HWND 取得 WGC 窗口帧"
    if source in {"print_window", "printwindow", "window_surface"}:
        return "warning", "WGC 不可用，当前使用同一窗口的 PrintWindow 表面兜底"
    if source in {"screen_region_clear", "screen", "desktop_region"}:
        return "warning", "当前使用未遮挡桌面区域兜底；窗口不能被遮挡或最小化"
    return "warning", f"已取得预览，但捕获来源需要确认：{source or 'unknown'}"


def classify_channel_probe(data: dict[str, Any]) -> tuple[str, str]:
    channels = list(data.get("channels") or [])
    available = [item for item in channels if item.get("available")]
    events = sum(int(item.get("event_count") or 0) for item in channels)
    lines = sum(int(item.get("line_count") or 0) for item in channels)
    if not available:
        return "failed", "UIA、Electron Accessibility 和 WGC/OCR 均不可用"
    if events:
        return "passed", f"三级采集已解析到 {events} 个互动事件"
    if lines:
        return "passed", f"三级采集已读取到 {lines} 行可见文本；开播后再验证真实互动事件"
    return "warning", "采集通道可调用，但当前没有可见互动文本；开播前空列表允许，开播后必须实测"


def classify_electron(data: dict[str, Any]) -> tuple[str, str]:
    if not data.get("available"):
        return "warning", "尚未找到可启用 Electron Accessibility 的直播伴侣进程"
    if data.get("enabled"):
        return "passed", "直播伴侣已启用 Chromium 强制无障碍文本树"
    return "warning", "Electron Accessibility 尚未启用；请仅在未开播时安全重启直播伴侣"


def classify_audio_routes(data: dict[str, Any]) -> tuple[str, str]:
    if data.get("ready") and data.get("isolated"):
        return "passed", "GPT_IN 与 GPT_OUT 已使用两组隔离虚拟声卡"
    warnings = "；".join(str(item) for item in data.get("warnings") or [])
    return "failed", warnings or "双虚拟声卡路由尚未就绪"


def _active_vtube_runtime(agent: Any, requested: str = "") -> Any | None:
    manager = getattr(agent, "vtube_studio", None)
    if manager is None:
        return None
    if requested and requested in manager.sessions:
        return manager.sessions[requested]
    candidates = list(manager.sessions.values())
    candidates.sort(key=lambda item: str(item.state.get("started_at") or ""), reverse=True)
    return next(
        (
            item
            for item in candidates
            if item.state.get("status") in {"active", "starting", "reconnecting"}
        ),
        None,
    )


def _live_audio_status(agent: Any) -> dict[str, Any]:
    manager = getattr(agent, "live_audio_setup", None)
    if manager is not None:
        return dict(manager.status())
    return {"available": False, "reason": "一键直播音频管理器尚未初始化"}


def _classify_live_audio(data: dict[str, Any]) -> tuple[str, str]:
    if not data.get("available", True):
        return "warning", str(data.get("reason") or "尚未运行一键直播音频配置")
    if data.get("route_ready") and (data.get("native_lipsync", {}).get("passed") or data.get("fallback_running")):
        mode = "原生口型" if data.get("native_lipsync", {}).get("passed") else "API 口型兜底"
        return "passed", f"直播语音路由已就绪，当前使用{mode}"
    if data.get("route_ready"):
        return "warning", "双虚拟声卡已就绪，但尚未记录口型验证结果"
    return "warning", "尚未执行音频页的一键直播语音与口型配置"


def _write_bundle(report: dict[str, Any], preview: dict[str, Any] | None, diagnostics: dict[str, Any] | None) -> Path:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = VALIDATION_DIR / f"aliver-staged-validation-{stamp}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "validation-report.json",
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
        )
        if diagnostics:
            nested = Path(str(diagnostics.get("path") or ""))
            if nested.exists() and nested.is_file():
                archive.write(nested, "collector-diagnostics.zip")
        if preview:
            for key, filename in (
                ("window_image", "collector-window.jpg"),
                ("region_image", "collector-ocr-region.png"),
            ):
                content = legacy._decode_data_url(preview.get(key))
                if content:
                    archive.writestr(filename, content)
    return path


async def _run_preflight(agent: Any, options: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    phase = "preflight"
    steps: list[dict[str, Any]] = []
    preview: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    collector = getattr(agent, "douyin_collector", None)

    if collector is None:
        steps.append(validation_step(
            "preflight.collector_manager",
            phase=phase,
            level="failed",
            message="Bridge 没有直播伴侣可视采集器",
        ))
    else:
        try:
            collector.update_config({"capture_join_notices": True})
        except Exception:
            pass
        steps.append(await _sync_step(
            "preflight.window_permissions",
            phase,
            lambda: legacy._permission_snapshot(collector),
            lambda data: (
                ("passed", "已定位直播伴侣主窗口，Bridge 与目标权限可检查")
                if data.get("window")
                else ("failed", "没有找到直播伴侣主窗口")
            ),
        ))
        if hasattr(collector, "electron_accessibility_status"):
            steps.append(await _sync_step(
                "preflight.electron_accessibility",
                phase,
                lambda: collector.electron_accessibility_status(refresh=True),
                classify_electron,
            ))
        steps.append(await _sync_step(
            "preflight.three_channel_probe",
            phase,
            collector.probe_channels,
            classify_channel_probe,
        ))
        preview_step = await _sync_step(
            "preflight.window_capture",
            phase,
            collector.preview,
            classify_capture_source,
        )
        if isinstance(preview_step.get("data"), dict):
            preview = dict(preview_step["data"])
            preview_step["data"] = legacy._preview_payload(preview)
        steps.append(preview_step)
        if hasattr(collector, "probe_uia"):
            steps.append(await _sync_step(
                "preflight.uia_tree",
                phase,
                collector.probe_uia,
                lambda data: (
                    ("passed", f"UIA 互动区域暴露了 {int(data.get('region_control_count') or data.get('control_count') or 0)} 个控件")
                    if int(data.get("region_control_count") or data.get("control_count") or 0) > 0
                    else ("warning", "UIA 当前未暴露互动控件；可使用 Electron Accessibility 或 WGC/OCR")
                ),
            ))
        diagnostics_step = await _sync_step(
            "preflight.collector_diagnostics",
            phase,
            collector.export_diagnostics,
            lambda data: ("passed", "已生成直播伴侣采集诊断包") if data.get("path") else ("warning", "诊断接口已执行但未返回文件路径"),
        )
        if isinstance(diagnostics_step.get("data"), dict):
            diagnostics = dict(diagnostics_step["data"])
        steps.append(diagnostics_step)

    steps.append(await _sync_step(
        "preflight.audio_routes",
        phase,
        agent.audio.get_routes,
        classify_audio_routes,
    ))
    steps.append(await _sync_step(
        "preflight.live_audio_lipsync",
        phase,
        lambda: _live_audio_status(agent),
        _classify_live_audio,
    ))

    runtime = _active_vtube_runtime(agent, str(options.get("session_id") or ""))
    if runtime is None:
        steps.append(validation_step(
            "preflight.avatar_session",
            phase=phase,
            level="warning",
            message="没有活动 VTube Studio 会话；启动数字人会话后再检查模型和动作",
        ))
    else:
        avatar = await legacy._avatar_validation(
            agent,
            {
                "session_id": runtime.session_id,
                "test_actions": bool(options.get("test_actions", True)),
                "test_mouth": bool(options.get("audible_mouth_test", False)),
            },
        )
        for item in avatar.get("steps") or []:
            name = str(item.get("name") or "avatar.unknown")
            ok = bool(item.get("ok"))
            message = "数字人检查通过" if ok else str(item.get("error") or "数字人检查失败")
            if name == "avatar.mouth_audio_route":
                message = "可听口型测试通过" if ok else "可听口型测试失败"
            steps.append(validation_step(
                f"preflight.{name}",
                phase=phase,
                level="passed" if ok else "failed",
                message=message,
                data=item.get("data"),
                error=item.get("error"),
                elapsed_ms=float(item.get("elapsed_ms") or 0.0),
            ))
        if not options.get("audible_mouth_test"):
            steps.append(validation_step(
                "preflight.audible_mouth_test",
                phase=phase,
                level="skipped",
                message="默认不播放嗡嗡测试音；需要时勾选“包含可听口型测试音”",
            ))
    return steps, preview, diagnostics


async def _run_live(agent: Any, options: dict[str, Any]) -> list[dict[str, Any]]:
    phase = "live"
    collector = getattr(agent, "douyin_collector", None)
    if collector is None:
        return [validation_step(
            "live.collector_event",
            phase=phase,
            level="failed",
            message="Bridge 没有直播伴侣可视采集器",
        )]

    status = collector.status()
    if not status.get("running"):
        return [validation_step(
            "live.collector_event",
            phase=phase,
            level="failed",
            message="可视采集器尚未启动；请先在直播调试中心启动采集",
            data=status,
        )]

    try:
        collector.update_config({"capture_join_notices": True})
    except Exception:
        pass
    baseline_events = int(status.get("event_count") or 0)
    baseline_sent = int(status.get("sent_count") or 0)
    started_epoch = time.time()
    timeout = max(10.0, min(float(options.get("live_timeout_seconds") or 60.0), 180.0))
    latest: dict[str, Any] | None = None

    while time.time() - started_epoch < timeout:
        current = collector.status()
        events = list(current.get("recent_events") or [])
        if int(current.get("event_count") or 0) > baseline_events:
            latest = dict(events[-1]) if events else None
            break
        await asyncio.sleep(0.5)

    if latest is None:
        return [validation_step(
            "live.collector_event",
            phase=phase,
            level="failed",
            message="等待真实互动超时；请让另一账号进入直播间、评论、关注或送出测试礼物",
            data={"timeout_seconds": timeout, "baseline_event_count": baseline_events},
            elapsed_ms=(time.time() - started_epoch) * 1000,
        )]

    current = collector.status()
    steps = [validation_step(
        "live.collector_event",
        phase=phase,
        level="passed",
        message=f"已识别真实互动：{latest.get('event_type')} · {latest.get('user_name')}",
        data=latest,
        elapsed_ms=(time.time() - started_epoch) * 1000,
    )]
    sent_delta = int(current.get("sent_count") or 0) - baseline_sent
    steps.append(validation_step(
        "live.server_forward",
        phase=phase,
        level="passed" if sent_delta > 0 else "warning",
        message=(
            f"互动已转发到 ALiver 服务端，新增 {sent_delta} 个事件"
            if sent_delta > 0
            else "Bridge 已识别互动，但服务端接收计数没有增加；请检查扩展选择和 Bridge 鉴权"
        ),
        data={
            "baseline_sent_count": baseline_sent,
            "sent_count": int(current.get("sent_count") or 0),
        },
    ))
    return steps


async def run_staged_validation(agent: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    options = dict(payload or {})
    phase = str(options.get("phase") or "preflight").strip().lower()
    if phase not in {"preflight", "live", "full"}:
        raise ValueError("phase must be preflight, live or full")

    steps: list[dict[str, Any]] = []
    preview: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    if phase in {"preflight", "full"}:
        preflight, preview, diagnostics = await _run_preflight(agent, options)
        steps.extend(preflight)
    if phase == "live" or (phase == "full" and bool(options.get("include_live", False))):
        steps.extend(await _run_live(agent, options))

    report = {
        "schema_version": 2,
        "created_at": _utc_now(),
        "phase": phase,
        "bridge": {
            "version": str(agent.system_info().get("bridge_version") or ""),
            "system_info": agent.system_info(),
        },
        "options": options,
        "steps": steps,
        "summary": summarize_steps(steps),
        "notes": {
            "preflight": "开播前可检查窗口、权限、采集能力、数字人、动作和静音音频状态。",
            "live": "必须开播后由另一账号产生真实互动，才能验证采集到导演的闭环。",
            "audible_test": "可听口型测试默认关闭，开启后直播伴侣可能播放约 3 秒测试音。",
        },
    }
    bundle = await asyncio.to_thread(_write_bundle, report, preview, diagnostics)
    return {
        "completed": True,
        "phase": phase,
        "summary": report["summary"],
        "report": report,
        "path": str(bundle),
        "folder": str(bundle.parent),
        "message": "分阶段直播验证已完成；通过、警告和失败已分别记录。",
    }
