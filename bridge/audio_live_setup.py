from __future__ import annotations

import asyncio
import time
from typing import Any

from bridge.full_validation import _mouth_validation

_ACTIVE_STATUSES = {"active", "starting", "running", "ready", "reconnecting"}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def dbfs_to_mouth_value(dbfs: float, *, gate_dbfs: float = -52.0, full_dbfs: float = -14.0) -> float:
    """Map a GPT_OUT level to a smooth 0..1 mouth-open target."""
    value = float(dbfs)
    if value <= gate_dbfs:
        return 0.0
    if value >= full_dbfs:
        return 1.0
    normalized = (value - gate_dbfs) / max(1.0, full_dbfs - gate_dbfs)
    return round(_clamp(normalized, 0.0, 1.0) ** 0.72, 4)


def _active_runtime(manager: Any, requested_session_id: str | None = None) -> Any | None:
    if manager is None:
        return None
    if requested_session_id:
        runtime = manager.sessions.get(requested_session_id)
        if runtime is not None:
            return runtime
    candidates = list(manager.sessions.values())
    candidates.sort(key=lambda item: str(item.state.get("started_at") or ""), reverse=True)
    return next(
        (item for item in candidates if str(item.state.get("status") or "") in _ACTIVE_STATUSES),
        candidates[0] if candidates else None,
    )


def _route_targets(scan: dict[str, Any]) -> dict[str, Any]:
    routes = dict(scan.get("routes") or {})
    out_capture = dict((routes.get("gpt_out") or {}).get("capture") or {})
    out_playback = dict((routes.get("gpt_out") or {}).get("playback") or {})
    in_playback = dict((routes.get("gpt_in") or {}).get("playback") or {})
    in_microphone = dict((routes.get("gpt_in") or {}).get("microphone") or {})
    out_family = str(out_capture.get("virtual_family") or out_playback.get("virtual_family") or "")
    out_pair = next(
        (dict(pair) for pair in scan.get("virtual_pairs") or [] if str(pair.get("family") or "") == out_family),
        {},
    )
    out_microphone = dict(out_pair.get("microphone") or {})
    return {
        "chrome_output": out_playback,
        "douyin_microphone": out_microphone,
        "vtube_microphone": out_microphone,
        "chatgpt_microphone": in_microphone,
        "gpt_in_playback": in_playback,
        "gpt_out_capture": out_capture,
    }


def _target_instructions(targets: dict[str, Any], *, native_failed: bool = False) -> dict[str, Any]:
    return {
        "chrome_output": (targets.get("chrome_output") or {}).get("name"),
        "chatgpt_microphone": (targets.get("chatgpt_microphone") or {}).get("name"),
        "douyin_microphone": (targets.get("douyin_microphone") or {}).get("name"),
        "vtube_microphone": (targets.get("vtube_microphone") or {}).get("name"),
        "vtube_native_required": bool(native_failed),
    }


def _input_parameter_names(runtime: Any) -> set[str]:
    capabilities = runtime.state.get("motion_capabilities") or {}
    rows = capabilities.get("input_parameters") if isinstance(capabilities, dict) else []
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }


def _fallback_parameters(runtime: Any) -> tuple[str, ...]:
    available = _input_parameter_names(runtime)
    preferred_voice = str(runtime.config.get("mouth_input_parameter") or "VoiceVolume").strip()
    candidates = [preferred_voice, "VoiceVolume", "MouthOpen", "MouthOpenY", "VoiceVolumePlusMouthOpen"]
    values: list[str] = []
    for name in candidates:
        if not name or name in values:
            continue
        if available and name not in available:
            continue
        values.append(name)
    if not values:
        values = [preferred_voice or "VoiceVolume", "MouthOpen"]
    return tuple(values)


class LiveAudioSetupManager:
    """Configure dual-cable routing and keep VTube mouth movement alive when native lipsync is absent."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self._lock = asyncio.Lock()
        self._mouth_task: asyncio.Task | None = None
        self._runtime: Any | None = None
        self._mouth_value = 0.0
        self._expected_capture_key = ""
        self._capture_owned = False
        self._state: dict[str, Any] = {
            "configured": False,
            "mode": "idle",
            "session_id": None,
            "native_lipsync": None,
            "api_mouth_fallback": False,
            "mouth_parameters": [],
            "last_dbfs": -96.0,
            "last_mouth_value": 0.0,
            "injected_frames": 0,
            "last_injected_at": None,
            "last_error": None,
            "updated_at": None,
            "targets": {},
            "route_ready": False,
        }

    def status(self) -> dict[str, Any]:
        task = self._mouth_task
        value = dict(self._state)
        value["fallback_running"] = bool(task and not task.done())
        value["audio_capture"] = self.agent.audio.status()
        native = value.get("native_lipsync")
        value["instructions"] = _target_instructions(
            dict(value.get("targets") or {}),
            native_failed=bool(value.get("session_id") and isinstance(native, dict) and not native.get("passed")),
        )
        return value

    async def _stop_fallback(self, *, reset: bool = True) -> None:
        task = self._mouth_task
        self._mouth_task = None
        runtime = self._runtime
        self._runtime = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if reset and runtime is not None and hasattr(runtime.client, "inject_parameters"):
            parameters = list(self._state.get("mouth_parameters") or [])
            if parameters:
                try:
                    await runtime.client.inject_parameters({name: 0.0 for name in parameters})
                except Exception:
                    pass
        self._mouth_value = 0.0
        self._state["api_mouth_fallback"] = False
        self._state["last_mouth_value"] = 0.0

    async def _ensure_capture(self) -> dict[str, Any]:
        current = self.agent.audio.status()
        current_key = str((current.get("device") or {}).get("key") or "")
        if (
            current.get("active")
            and self._expected_capture_key
            and current_key
            and current_key != self._expected_capture_key
        ):
            await asyncio.to_thread(self.agent.audio.stop)
            current = self.agent.audio.status()
        if not current.get("active"):
            current = await asyncio.to_thread(
                self.agent.audio.start_gpt_out,
                duration_seconds=60.0,
                save_wav=False,
                auto_stop=False,
                chunk_size=512,
            )
            self._capture_owned = True
        return current

    async def _release_capture(self) -> None:
        if self._capture_owned and self.agent.audio.status().get("active"):
            await asyncio.to_thread(self.agent.audio.stop)
        self._capture_owned = False

    async def _fallback_loop(self, runtime: Any, parameters: tuple[str, ...]) -> None:
        attack = 0.58
        release = 0.20
        last_injected = -1.0
        try:
            while self._runtime is runtime:
                manager = getattr(self.agent, "vtube_studio", None)
                if manager is None or manager.sessions.get(runtime.session_id) is not runtime:
                    break
                capture = await self._ensure_capture()
                dbfs = float(capture.get("dbfs", -96.0))
                target = dbfs_to_mouth_value(dbfs)
                alpha = attack if target > self._mouth_value else release
                self._mouth_value += (target - self._mouth_value) * alpha
                value = round(_clamp(self._mouth_value, 0.0, 1.0), 4)
                self._state["last_dbfs"] = round(dbfs, 2)
                self._state["last_mouth_value"] = value
                if abs(value - last_injected) >= 0.012 or value == 0.0:
                    await runtime.client.inject_parameters({name: value for name in parameters})
                    last_injected = value
                    self._state["injected_frames"] = int(self._state.get("injected_frames") or 0) + 1
                    self._state["last_injected_at"] = time.time()
                await asyncio.sleep(1.0 / 15.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state["last_error"] = f"{type(exc).__name__}: {exc}"
            self._state["mode"] = "fallback_error"
        finally:
            owns_runtime = self._runtime is runtime
            if owns_runtime:
                self._runtime = None
                await self._release_capture()
            self._state["api_mouth_fallback"] = False

    async def _start_fallback(self, runtime: Any) -> None:
        await self._stop_fallback(reset=True)
        if not hasattr(runtime.client, "inject_parameters"):
            raise RuntimeError("当前 VTube Studio Bridge 不支持参数注入口型兜底")
        parameters = _fallback_parameters(runtime)
        await self._ensure_capture()
        self._runtime = runtime
        self._state["session_id"] = runtime.session_id
        self._state["mouth_parameters"] = list(parameters)
        self._state["api_mouth_fallback"] = True
        self._state["mode"] = "api_mouth_fallback"
        self._state["last_error"] = None
        self._mouth_task = asyncio.create_task(
            self._fallback_loop(runtime, parameters),
            name=f"aliver-audio-mouth-{runtime.session_id}",
        )

    async def auto_configure(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        options = dict(payload or {})
        async with self._lock:
            await asyncio.to_thread(self.agent.audio.apply_recommendations)
            scan = await asyncio.to_thread(self.agent.audio.list_devices)
            targets = _route_targets(scan)
            route_ready = bool((scan.get("routes") or {}).get("ready"))
            self._expected_capture_key = str((targets.get("gpt_out_capture") or {}).get("key") or "")
            if not route_ready:
                warnings = (scan.get("routes") or {}).get("warnings") or []
                raise RuntimeError(warnings[0] if warnings else "双虚拟声卡路由尚未就绪")

            manager = getattr(self.agent, "vtube_studio", None)
            runtime = _active_runtime(manager, str(options.get("session_id") or "").strip() or None)
            native_result: dict[str, Any] | None = None
            fallback_enabled = False
            vtube_microphone = str((targets.get("vtube_microphone") or {}).get("name") or "")

            if runtime is not None:
                if vtube_microphone:
                    runtime.config["audio_device_name"] = vtube_microphone
                try:
                    native_result = await _mouth_validation(self.agent, runtime)
                except Exception as exc:
                    native_result = {
                        "passed": False,
                        "diagnosis": f"VTube Studio 原生口型验证异常：{type(exc).__name__}: {exc}",
                    }
                if native_result.get("passed"):
                    await self._stop_fallback(reset=True)
                    await self._release_capture()
                    self._state["mode"] = "native_vtube_lipsync"
                elif bool(options.get("enable_api_fallback", True)):
                    await self._start_fallback(runtime)
                    fallback_enabled = True
                else:
                    await self._stop_fallback(reset=True)
                    await self._release_capture()
                    self._state["mode"] = "native_lipsync_failed"
            else:
                await self._stop_fallback(reset=True)
                await self._release_capture()
                self._state["mode"] = "routes_ready_waiting_vtube"

            self._state.update(
                {
                    "configured": True,
                    "session_id": runtime.session_id if runtime is not None else None,
                    "native_lipsync": native_result,
                    "api_mouth_fallback": fallback_enabled,
                    "targets": targets,
                    "route_ready": route_ready,
                    "updated_at": time.time(),
                    "last_error": None,
                }
            )
            return {**self.status(), "scan": scan}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            await self._stop_fallback(reset=True)
            await self._release_capture()
            self._state["mode"] = "stopped"
            self._state["configured"] = False
            self._state["updated_at"] = time.time()
            return self.status()
