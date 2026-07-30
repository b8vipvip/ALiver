from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import time
import zipfile
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from bridge.audio_capture import _load_pyaudio, normalize_device_name, virtual_family

VALIDATION_DIR = Path(__file__).resolve().parent / "logs" / "full_validation"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _is_process_elevated(pid: int) -> bool | None:
    if os.name != "nt" or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        token_query = 0x0008
        token_elevation = 20

        class TokenElevation(ctypes.Structure):
            _fields_ = [("TokenIsElevated", wintypes.DWORD)]

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        process = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not process:
            return None
        try:
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(process, token_query, ctypes.byref(token)):
                return None
            try:
                value = TokenElevation()
                returned = wintypes.DWORD()
                if not advapi32.GetTokenInformation(
                    token,
                    token_elevation,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                    ctypes.byref(returned),
                ):
                    return None
                return bool(value.TokenIsElevated)
            finally:
                kernel32.CloseHandle(token)
        finally:
            kernel32.CloseHandle(process)
    except Exception:
        return None


def _step(name: str, *, ok: bool, status: str, data: Any = None, error: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "status": status,
        "data": data,
        "error": error,
        "finished_at": _utc_now(),
    }


async def _run_async_step(
    name: str,
    callback: Callable[[], Awaitable[Any]],
    *,
    success_status: str = "passed",
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        data = await callback()
        result = _step(name, ok=True, status=success_status, data=data)
    except Exception as exc:  # noqa: BLE001 - validation must continue after every failure
        result = _step(name, ok=False, status="failed", error=_error(exc))
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return result


async def _run_sync_step(
    name: str,
    callback: Callable[[], Any],
    *,
    success_status: str = "passed",
) -> dict[str, Any]:
    return await _run_async_step(name, lambda: asyncio.to_thread(callback), success_status=success_status)


def _preview_payload(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in preview.items()
        if key not in {"window_image", "region_image"}
    }


def _decode_data_url(value: Any) -> bytes | None:
    text = str(value or "")
    if not text.startswith("data:") or "," not in text:
        return None
    try:
        return base64.b64decode(text.split(",", 1)[1])
    except Exception:
        return None


def _find_audio_pair(audio_manager: Any, microphone_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    devices = audio_manager.list_devices()
    pairs = list(devices.get("virtual_pairs") or [])
    wanted = normalize_device_name(microphone_name)
    wanted_family = virtual_family(microphone_name)
    selected = None
    for pair in pairs:
        microphone = pair.get("microphone") or {}
        mic_name = normalize_device_name(str(microphone.get("name") or ""))
        if wanted and (wanted == mic_name or wanted in mic_name or mic_name in wanted):
            selected = pair
            break
    if selected is None and wanted_family:
        selected = next((pair for pair in pairs if pair.get("family") == wanted_family), None)
    if selected is None:
        raise RuntimeError(f"没有找到与 VTube Studio 麦克风“{microphone_name}”配对的虚拟播放设备")
    playback = selected.get("playback") or {}
    if playback.get("index") is None:
        raise RuntimeError("虚拟音频线存在，但没有可用的播放端设备")
    return devices, selected


def _play_mouth_test_signal(audio_manager: Any, microphone_name: str, seconds: float = 2.4) -> dict[str, Any]:
    devices, pair = _find_audio_pair(audio_manager, microphone_name)
    playback = dict(pair.get("playback") or {})
    pyaudio = _load_pyaudio()
    audio = pyaudio.PyAudio()
    stream = None
    try:
        info = dict(audio.get_device_info_by_index(int(playback["index"])))
        channels = max(1, min(int(info.get("maxOutputChannels") or 2), 2))
        sample_rate = int(float(info.get("defaultSampleRate") or 48000))
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            output=True,
            output_device_index=int(playback["index"]),
            frames_per_buffer=1024,
        )
        total_frames = int(sample_rate * max(1.0, min(float(seconds), 6.0)))
        offset = 0
        while offset < total_frames:
            count = min(1024, total_frames - offset)
            samples = array("h")
            for index in range(count):
                t = (offset + index) / sample_rate
                envelope = 0.18 + 0.82 * (math.sin(math.pi * 3.2 * t) ** 2)
                carrier = math.sin(2.0 * math.pi * 220.0 * t) + 0.35 * math.sin(2.0 * math.pi * 440.0 * t)
                sample = int(max(-1.0, min(1.0, carrier * envelope * 0.23)) * 32767)
                for _ in range(channels):
                    samples.append(sample)
            stream.write(samples.tobytes())
            offset += count
        stream.write(bytes(int(sample_rate * 0.25) * channels * 2))
        return {
            "played": True,
            "duration_seconds": round(total_frames / sample_rate, 3),
            "sample_rate": sample_rate,
            "channels": channels,
            "playback_device": playback,
            "microphone_device": pair.get("microphone"),
            "virtual_family": pair.get("family"),
            "available_pairs": len(devices.get("virtual_pairs") or []),
        }
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
        audio.terminate()


async def _sample_parameter(client: Any, name: str) -> dict[str, Any]:
    values: list[float] = []
    errors: list[str] = []
    for _ in range(4):
        try:
            values.append(float(await client.parameter_value(name)))
        except Exception as exc:  # noqa: BLE001
            errors.append(_error(exc))
        await asyncio.sleep(0.08)
    return {
        "name": name,
        "values": values,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "range": (max(values) - min(values)) if values else None,
        "errors": errors,
    }


async def _mouth_validation(agent: Any, runtime: Any) -> dict[str, Any]:
    config = dict(runtime.config or {})
    client = runtime.client
    voice_parameter = str(config.get("mouth_input_parameter") or "VoiceVolume")
    mouth_parameter = str(config.get("mouth_output_parameter") or "ParamMouthOpenY")
    microphone_name = str(config.get("audio_device_name") or "CABLE Output (VB-Audio Virtual Cable)")

    baseline = {
        voice_parameter: await _sample_parameter(client, voice_parameter),
        mouth_parameter: await _sample_parameter(client, mouth_parameter),
    }
    playback_task = asyncio.create_task(
        asyncio.to_thread(_play_mouth_test_signal, agent.audio, microphone_name),
        name="aliver-mouth-test-playback",
    )
    observed: dict[str, list[float]] = {voice_parameter: [], mouth_parameter: []}
    errors: dict[str, list[str]] = {voice_parameter: [], mouth_parameter: []}
    deadline = time.monotonic() + 4.5
    while time.monotonic() < deadline and not playback_task.done():
        for name in (voice_parameter, mouth_parameter):
            try:
                observed[name].append(float(await client.parameter_value(name)))
            except Exception as exc:  # noqa: BLE001
                errors[name].append(_error(exc))
        await asyncio.sleep(0.10)
    playback = await playback_task
    for _ in range(5):
        for name in (voice_parameter, mouth_parameter):
            try:
                observed[name].append(float(await client.parameter_value(name)))
            except Exception as exc:  # noqa: BLE001
                errors[name].append(_error(exc))
        await asyncio.sleep(0.08)

    parameters: dict[str, Any] = {}
    for name in (voice_parameter, mouth_parameter):
        values = observed[name]
        base_max = baseline[name].get("maximum") or 0.0
        maximum = max(values) if values else None
        minimum = min(values) if values else None
        parameters[name] = {
            "baseline": baseline[name],
            "samples": values,
            "minimum": minimum,
            "maximum": maximum,
            "range": (maximum - minimum) if values else None,
            "rise_over_baseline": (maximum - base_max) if values else None,
            "errors": errors[name],
        }
    voice_max = parameters[voice_parameter].get("maximum") or 0.0
    mouth_max = parameters[mouth_parameter].get("maximum") or 0.0
    voice_rise = parameters[voice_parameter].get("rise_over_baseline") or 0.0
    mouth_rise = parameters[mouth_parameter].get("rise_over_baseline") or 0.0
    passed = bool(voice_max >= 0.025 or mouth_max >= 0.025 or voice_rise >= 0.015 or mouth_rise >= 0.015)
    return {
        "passed": passed,
        "diagnosis": (
            "虚拟音频已到达 VTube Studio，口型参数产生变化"
            if passed
            else "测试音已播放，但 VoiceVolume 与 ParamMouthOpenY 未产生足够变化；请检查 VTube Studio 麦克风和口型映射"
        ),
        "playback": playback,
        "parameters": parameters,
    }


async def _avatar_validation(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    manager = getattr(agent, "vtube_studio", None)
    if manager is None:
        return {"available": False, "reason": "Bridge 没有 VTube Studio 管理器", "steps": []}
    requested = str(payload.get("session_id") or "").strip()
    runtime = manager.sessions.get(requested) if requested else None
    if runtime is None:
        candidates = list(manager.sessions.values())
        candidates.sort(key=lambda item: str(item.state.get("started_at") or ""), reverse=True)
        runtime = next((item for item in candidates if item.state.get("status") in {"active", "starting", "reconnecting"}), None)
    if runtime is None:
        return {"available": False, "reason": "没有活动 VTube Studio 会话", "steps": []}

    session_id = runtime.session_id
    steps: list[dict[str, Any]] = []
    steps.append(await _run_async_step("avatar.connection_model", lambda: manager.refresh(session_id)))
    scan = await _run_async_step("avatar.motion_capabilities", lambda: manager.motion_scan(session_id))
    steps.append(scan)

    if bool(payload.get("test_actions", True)):
        supported = set(((scan.get("data") or {}).get("capabilities") or {}).get("supported_actions") or [])
        action_results: list[dict[str, Any]] = []
        for action in ("thinking", "happy", "surprised", "wave"):
            if supported and action not in supported and not (runtime.config.get("hotkeys") or {}).get(action):
                action_results.append({"action": action, "ok": False, "status": "unsupported"})
                continue
            try:
                result = await manager.action(session_id, action=action, force=True)
                action_results.append({"action": action, "ok": True, "result": result.get("action_result")})
            except Exception as exc:  # noqa: BLE001
                action_results.append({"action": action, "ok": False, "error": _error(exc)})
            await asyncio.sleep(0.35)
        try:
            reset = await manager.action(session_id, action="reset", force=True)
            action_results.append({"action": "reset", "ok": True, "result": reset.get("action_result")})
        except Exception as exc:  # noqa: BLE001
            action_results.append({"action": "reset", "ok": False, "error": _error(exc)})
        steps.append(
            _step(
                "avatar.actions",
                ok=any(item.get("ok") for item in action_results),
                status="passed" if all(item.get("ok") or item.get("status") == "unsupported" for item in action_results) else "partial",
                data=action_results,
            )
        )

    if bool(payload.get("test_mouth", True)):
        mouth = await _run_async_step("avatar.mouth_audio_route", lambda: _mouth_validation(agent, runtime))
        if mouth.get("ok") and isinstance(mouth.get("data"), dict):
            mouth["ok"] = bool(mouth["data"].get("passed"))
            mouth["status"] = "passed" if mouth["ok"] else "failed"
        steps.append(mouth)

    return {
        "available": True,
        "session_id": session_id,
        "provider_name": runtime.provider_name,
        "model": (runtime.status().get("model") or {}),
        "steps": steps,
    }


def _permission_snapshot(collector_manager: Any) -> dict[str, Any]:
    window = collector_manager._find_window()
    process = collector_manager._selected_window_process(window) if hasattr(collector_manager, "_selected_window_process") else {}
    target_pid = int(process.get("pid") or 0)
    bridge_elevated = _is_process_elevated(os.getpid())
    target_elevated = _is_process_elevated(target_pid)
    return {
        "bridge_pid": os.getpid(),
        "bridge_elevated": bridge_elevated,
        "target_pid": target_pid,
        "target_elevated": target_elevated,
        "same_elevation": (
            bridge_elevated == target_elevated
            if bridge_elevated is not None and target_elevated is not None
            else None
        ),
        "window": window.as_dict() if window is not None else None,
        "process": process,
    }


def _write_bundle(report: dict[str, Any], collector_diagnostics: dict[str, Any] | None, preview: dict[str, Any] | None) -> Path:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = VALIDATION_DIR / f"aliver-full-validation-{stamp}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("validation-report.json", json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if collector_diagnostics:
            nested = Path(str(collector_diagnostics.get("path") or ""))
            if nested.exists() and nested.is_file():
                archive.write(nested, "collector-diagnostics.zip")
        if preview:
            window_image = _decode_data_url(preview.get("window_image"))
            region_image = _decode_data_url(preview.get("region_image"))
            if window_image:
                archive.writestr("collector-window.jpg", window_image)
            if region_image:
                archive.writestr("collector-ocr-region.png", region_image)
    return path


async def run_full_validation(agent: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    options = dict(payload or {})
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "bridge": {
            "version": str(agent.system_info().get("bridge_version") or ""),
            "system_info": agent.system_info(),
        },
        "options": options,
        "collector_steps": [],
        "avatar": None,
    }
    collector_manager = getattr(agent, "douyin_collector", None)
    preview: dict[str, Any] | None = None
    collector_diagnostics: dict[str, Any] | None = None
    if collector_manager is None:
        report["collector_steps"].append(
            _step("collector.manager", ok=False, status="missing", error="Bridge 没有抖音可视采集器")
        )
    else:
        report["collector_steps"].append(
            await _run_sync_step("collector.window_permissions", lambda: _permission_snapshot(collector_manager))
        )
        report["collector_steps"].append(
            await _run_sync_step("collector.three_channels", collector_manager.probe_channels)
        )
        preview_step = await _run_sync_step("collector.wgc_preview", collector_manager.preview)
        if preview_step.get("ok"):
            preview = dict(preview_step.get("data") or {})
            preview_step["data"] = _preview_payload(preview)
        report["collector_steps"].append(preview_step)
        diagnostics_step = await _run_sync_step("collector.diagnostics", collector_manager.export_diagnostics)
        if diagnostics_step.get("ok"):
            collector_diagnostics = dict(diagnostics_step.get("data") or {})
        report["collector_steps"].append(diagnostics_step)

    report["avatar"] = await _avatar_validation(agent, options)
    all_steps = list(report["collector_steps"]) + list((report["avatar"] or {}).get("steps") or [])
    passed = sum(1 for item in all_steps if item.get("ok"))
    failed = sum(1 for item in all_steps if not item.get("ok") and item.get("status") not in {"skipped", "missing"})
    report["summary"] = {
        "passed": passed,
        "failed": failed,
        "total": len(all_steps),
        "overall": "passed" if failed == 0 and all_steps else "partial" if passed else "failed",
    }
    bundle_path = await asyncio.to_thread(_write_bundle, report, collector_diagnostics, preview)
    return {
        "completed": True,
        "summary": report["summary"],
        "report": report,
        "path": str(bundle_path),
        "folder": str(bundle_path.parent),
        "message": "一键完整验证已执行完毕；所有成功和失败步骤都已写入 ZIP。",
    }
