from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from app.vtube_motion_config import MOTION_ACTIONS, normalize_motion_engine
from bridge import vtube_studio as vtube

ROLE_ALIASES = {
    "angle_x": ("FaceAngleX",),
    "angle_y": ("FaceAngleY",),
    "angle_z": ("FaceAngleZ",),
    "position_x": ("FacePositionX",),
    "position_y": ("FacePositionY",),
    "eye_open_left": ("EyeOpenLeft",),
    "eye_open_right": ("EyeOpenRight",),
    "eye_y_left": ("EyeLeftY",),
    "eye_y_right": ("EyeRightY",),
    "brow_y_left": ("BrowLeftY",),
    "brow_y_right": ("BrowRightY",),
    "mouth_smile": ("MouthSmile",),
}

EXPRESSION_KEYWORDS = {
    "thinking": (
        "think",
        "thinking",
        "confus",
        "question",
        "ponder",
        "思考",
        "疑问",
        "困惑",
        "考え",
    ),
    "happy": (
        "happy",
        "smile",
        "joy",
        "laugh",
        "blush",
        "开心",
        "高兴",
        "微笑",
        "笑",
        "嬉",
    ),
    "surprised": (
        "surpris",
        "shock",
        "wow",
        "惊讶",
        "震惊",
        "びっくり",
        "驚",
    ),
}

ACTION_DURATIONS = {
    "idle": 1.8,
    "talking": 3.0,
    "thinking": 3.0,
    "wave": 2.5,
    "happy": 2.8,
    "surprised": 1.8,
}


def _utc_epoch() -> float:
    return time.time()


def _parameter_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("defaultParameters", "customParameters", "parameters"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    deduplicated: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if name:
            deduplicated[name] = dict(row)
    return list(deduplicated.values())


def _expression_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("expressions")
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _recommend_expressions(expressions: list[dict[str, Any]]) -> dict[str, str]:
    result = {"thinking": "", "happy": "", "surprised": ""}
    for action, keywords in EXPRESSION_KEYWORDS.items():
        for item in expressions:
            haystack = f"{item.get('name', '')} {item.get('file', '')}".casefold()
            if any(keyword.casefold() in haystack for keyword in keywords):
                result[action] = str(item.get("file") or "").strip()
                break
    return result


def _role_map(parameters: list[dict[str, Any]]) -> dict[str, str]:
    names = {str(row.get("name") or "").strip() for row in parameters}
    result: dict[str, str] = {}
    for role, aliases in ROLE_ALIASES.items():
        match = next((name for name in aliases if name in names), "")
        if match:
            result[role] = match
    return result


def build_motion_capabilities(
    input_payload: dict[str, Any],
    live2d_payload: dict[str, Any],
    expression_payload: dict[str, Any],
) -> dict[str, Any]:
    input_parameters = _parameter_rows(input_payload)
    live2d_parameters = _parameter_rows(live2d_payload)
    expressions = _expression_rows(expression_payload)
    roles = _role_map(input_parameters)
    recommended = normalize_motion_engine(
        {
            "enabled": True,
            "expression_map": _recommend_expressions(expressions),
        }
    )
    supported_actions = ["idle", "talking", "thinking", "happy", "surprised", "reset"]
    if roles:
        supported_actions.insert(3, "wave")
    return {
        "model_loaded": bool(
            input_payload.get("modelLoaded")
            or live2d_payload.get("modelLoaded")
            or expression_payload.get("modelLoaded")
        ),
        "model_name": (
            input_payload.get("modelName")
            or live2d_payload.get("modelName")
            or expression_payload.get("modelName")
        ),
        "model_id": (
            input_payload.get("modelID")
            or live2d_payload.get("modelID")
            or expression_payload.get("modelID")
        ),
        "input_parameters": input_parameters,
        "live2d_parameters": live2d_parameters,
        "expressions": expressions,
        "role_map": roles,
        "counts": {
            "input_parameters": len(input_parameters),
            "live2d_parameters": len(live2d_parameters),
            "expressions": len(expressions),
            "resolved_motion_roles": len(roles),
        },
        "supported_actions": supported_actions,
        "recommended_motion_engine": recommended,
        "limitations": {
            "wave_is_procedural_greeting": True,
            "can_create_hotkeys": False,
            "can_create_motion3_assets": False,
            "requires_existing_standard_parameter_mappings": True,
        },
    }


class VTubeMotionEngine:
    def __init__(
        self,
        client: Any,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None,
    ) -> None:
        self.client = client
        self.config = normalize_motion_engine(config)
        self.capabilities = dict(capabilities or {})
        self._parameter_specs: dict[str, dict[str, Any]] = {}
        self._role_map: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._started_monotonic = time.monotonic()
        self._last_voice_poll = 0.0
        self._last_voice_at = 0.0
        self._voice_value = 0.0
        self._speaking = False
        self._mode = "disabled"
        self._transient_action: str | None = None
        self._transient_started = 0.0
        self._transient_until = 0.0
        self._active_expression: str | None = None
        self._expression_until = 0.0
        self._last_error: str | None = None
        self._last_injected_at: float | None = None
        self._injected_frames = 0
        self._refresh_specs()

    def _refresh_specs(self) -> None:
        rows = self.capabilities.get("input_parameters")
        if not isinstance(rows, list):
            rows = []
        self._parameter_specs = {
            str(row.get("name") or ""): dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("name") or "").strip()
        }
        roles = self.capabilities.get("role_map")
        self._role_map = dict(roles) if isinstance(roles, dict) else _role_map(rows)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        supported = self.capabilities.get("supported_actions")
        return {
            "enabled": bool(self.config.get("enabled")),
            "running": self.running,
            "preset": self.config.get("preset"),
            "fps": self.config.get("fps"),
            "auto_speech": self.config.get("auto_speech"),
            "voice_parameter": self.config.get("voice_parameter"),
            "speech_threshold": self.config.get("speech_threshold"),
            "voice_value": round(self._voice_value, 4),
            "speaking": self._speaking,
            "current_mode": self._mode,
            "transient_action": self._transient_action,
            "active_expression": self._active_expression,
            "supported_actions": list(supported) if isinstance(supported, list) else [],
            "role_map": dict(self._role_map),
            "injected_frames": self._injected_frames,
            "last_injected_at": self._last_injected_at,
            "last_error": self._last_error,
            "config": dict(self.config),
        }

    async def configure(
        self,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = dict(self.config)
        if isinstance(config, dict):
            merged.update(config)
        self.config = normalize_motion_engine(merged)
        if capabilities is not None:
            self.capabilities = dict(capabilities)
            self._refresh_specs()
        if self.config["enabled"]:
            await self.start()
        else:
            await self.stop(reset=True)
        return self.status()

    async def start(self) -> None:
        self.config["enabled"] = True
        if self.running:
            return
        self._stop = asyncio.Event()
        self._started_monotonic = time.monotonic()
        self._task = asyncio.create_task(self._run(), name="vtube-natural-motion")

    async def stop(self, *, reset: bool = True) -> None:
        self.config["enabled"] = False
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if reset:
            await self.reset()
        self._mode = "disabled"

    async def reset(self) -> dict[str, Any]:
        self._transient_action = None
        self._transient_until = 0.0
        await self._deactivate_expression()
        neutral = self._neutral_values()
        if neutral:
            try:
                await self.client.inject_parameters(neutral)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
        return self.status()

    async def trigger(self, action: str) -> dict[str, Any]:
        name = str(action or "").strip().lower()
        if name not in MOTION_ACTIONS:
            raise ValueError(f"Unsupported VTube procedural action: {name or 'empty'}")
        if name == "reset":
            await self.reset()
            return {
                "triggered": True,
                "procedural": True,
                "action": "reset",
                "mode": self._mode,
            }
        if not self.config.get("enabled"):
            await self.start()
        now = time.monotonic()
        self._transient_action = name
        self._transient_started = now
        self._transient_until = now + ACTION_DURATIONS.get(name, 2.0)
        await self._activate_action_expression(name)
        return {
            "triggered": True,
            "procedural": True,
            "action": name,
            "duration_seconds": ACTION_DURATIONS.get(name, 2.0),
            "expression": self._active_expression,
        }

    async def _activate_action_expression(self, action: str) -> None:
        if not self.config.get("expressions_enabled"):
            return
        mapping = self.config.get("expression_map")
        expression_file = str((mapping or {}).get(action) or "").strip()
        if not expression_file:
            return
        if self._active_expression and self._active_expression != expression_file:
            await self._deactivate_expression()
        try:
            await self.client.activate_expression(expression_file, active=True, fade_time=0.2)
            self._active_expression = expression_file
            self._expression_until = time.monotonic() + ACTION_DURATIONS.get(action, 2.0)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"

    async def _deactivate_expression(self) -> None:
        expression = self._active_expression
        self._active_expression = None
        self._expression_until = 0.0
        if not expression:
            return
        try:
            await self.client.activate_expression(expression, active=False, fade_time=0.2)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"

    async def _poll_voice(self, now: float) -> None:
        if not self.config.get("auto_speech"):
            self._voice_value = 0.0
            self._speaking = False
            return
        if now - self._last_voice_poll < 0.12:
            return
        self._last_voice_poll = now
        try:
            value = await self.client.parameter_value(self.config["voice_parameter"])
            self._voice_value = max(0.0, float(value))
            if self._voice_value >= float(self.config["speech_threshold"]):
                self._last_voice_at = now
            hold = float(self.config["speech_hold_ms"]) / 1000.0
            self._speaking = now - self._last_voice_at <= hold
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._speaking = False

    def _current_mode(self, now: float) -> str:
        if self._transient_action and now <= self._transient_until:
            return self._transient_action
        if self._transient_action:
            self._transient_action = None
            self._transient_until = 0.0
        return "talking" if self._speaking else "idle"

    def _put(self, result: dict[str, float], role: str, value: float) -> None:
        name = self._role_map.get(role)
        if not name:
            return
        spec = self._parameter_specs.get(name) or {}
        minimum = float(spec.get("min", -1_000_000))
        maximum = float(spec.get("max", 1_000_000))
        result[name] = max(minimum, min(value, maximum))

    def _neutral_values(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for role, name in self._role_map.items():
            if role not in {
                "angle_x",
                "angle_y",
                "angle_z",
                "position_x",
                "position_y",
                "mouth_smile",
                "brow_y_left",
                "brow_y_right",
            }:
                continue
            spec = self._parameter_specs.get(name) or {}
            result[name] = float(spec.get("defaultValue", 0.0))
        return result

    def _values_for(self, mode: str, now: float) -> dict[str, float]:
        elapsed = now - self._started_monotonic
        action_elapsed = max(0.0, now - self._transient_started)
        lively = 1.28 if self.config.get("preset") == "lively" else 1.0
        if mode == "talking":
            intensity = float(self.config["talking_intensity"]) * lively
            speed = 1.45
        elif mode == "idle":
            intensity = float(self.config["idle_intensity"]) * lively
            speed = 0.72
        else:
            intensity = float(self.config["action_intensity"]) * lively
            speed = 1.0

        values: dict[str, float] = {}
        self._put(
            values,
            "angle_x",
            intensity * (4.6 * math.sin(elapsed * 0.72 * speed) + 1.5 * math.sin(elapsed * 0.21)),
        )
        self._put(values, "angle_y", intensity * 2.8 * math.sin(elapsed * 0.48 * speed + 1.1))
        self._put(values, "angle_z", intensity * 3.3 * math.sin(elapsed * 0.39 * speed + 2.0))
        self._put(values, "position_x", intensity * 0.22 * math.sin(elapsed * 0.31 * speed))
        self._put(values, "position_y", intensity * 0.42 * math.sin(elapsed * 0.63 * speed))

        if mode == "talking":
            self._put(
                values,
                "angle_x",
                intensity * (5.6 * math.sin(elapsed * 1.15) + 1.8 * math.sin(elapsed * 0.41)),
            )
            self._put(values, "position_y", intensity * 0.65 * math.sin(elapsed * 1.7))
            self._put(values, "mouth_smile", min(0.42, 0.12 + self._voice_value * 0.28))
        elif mode == "thinking":
            self._put(values, "angle_z", intensity * (8.0 + 1.2 * math.sin(action_elapsed * 1.3)))
            self._put(values, "angle_y", intensity * (-4.0 + 1.0 * math.sin(action_elapsed * 0.8)))
            self._put(values, "eye_y_left", 0.65)
            self._put(values, "eye_y_right", 0.65)
            self._put(values, "brow_y_left", 0.35)
            self._put(values, "brow_y_right", 0.35)
        elif mode == "wave":
            # VTube Studio cannot create missing arm rigging through the public API.
            # This produces an unmistakable greeting sway; an existing arm hotkey can be layered on top.
            self._put(values, "angle_x", intensity * 7.5 * math.sin(action_elapsed * 4.6))
            self._put(values, "angle_z", intensity * 6.0 * math.sin(action_elapsed * 4.6 + 0.8))
            self._put(values, "position_y", intensity * abs(math.sin(action_elapsed * 4.6)) * 1.1)
            self._put(values, "mouth_smile", 0.82)
        elif mode == "happy":
            self._put(values, "angle_x", intensity * 4.0 * math.sin(action_elapsed * 2.7))
            self._put(values, "position_y", intensity * abs(math.sin(action_elapsed * 3.2)) * 1.0)
            self._put(values, "mouth_smile", 0.95)
            self._put(values, "eye_open_left", 0.72)
            self._put(values, "eye_open_right", 0.72)
            self._put(values, "brow_y_left", 0.45)
            self._put(values, "brow_y_right", 0.45)
        elif mode == "surprised":
            self._put(values, "angle_y", intensity * 7.0)
            self._put(values, "position_y", intensity * 1.2)
            self._put(values, "eye_open_left", 1.0)
            self._put(values, "eye_open_right", 1.0)
            self._put(values, "brow_y_left", 1.0)
            self._put(values, "brow_y_right", 1.0)
            self._put(values, "mouth_smile", 0.05)

        return values

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                await self._poll_voice(started)
                mode = self._current_mode(started)
                self._mode = mode
                if self._active_expression and started > self._expression_until:
                    await self._deactivate_expression()
                values = self._values_for(mode, started)
                if values:
                    try:
                        await self.client.inject_parameters(values)
                        self._injected_frames += 1
                        self._last_injected_at = _utc_epoch()
                        self._last_error = None
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._last_error = f"{type(exc).__name__}: {exc}"
                frame_time = 1.0 / max(5, int(self.config["fps"]))
                await asyncio.sleep(max(0.005, frame_time - (time.monotonic() - started)))
        except asyncio.CancelledError:
            raise


async def _client_input_parameters(self: Any) -> dict[str, Any]:
    return await self._request("InputParameterListRequest")


async def _client_live2d_parameters(self: Any) -> dict[str, Any]:
    return await self._request("Live2DParameterListRequest")


async def _client_expressions(self: Any) -> dict[str, Any]:
    return await self._request(
        "ExpressionStateRequest",
        {"details": True, "expressionFile": ""},
    )


async def _client_inspect_capabilities(self: Any) -> dict[str, Any]:
    input_payload, live2d_payload, expression_payload = await asyncio.gather(
        self.input_parameters(),
        self.live2d_parameters(),
        self.expressions(),
    )
    return build_motion_capabilities(input_payload, live2d_payload, expression_payload)


async def _client_parameter_value(self: Any, name: str) -> float:
    data = await self._request("ParameterValueRequest", {"name": str(name)})
    return float(data.get("value", 0.0))


async def _client_inject_parameters(
    self: Any,
    values: dict[str, float],
    *,
    mode: str = "set",
) -> dict[str, Any]:
    parameter_values = [
        {"id": str(name), "value": float(value), "weight": 1.0}
        for name, value in values.items()
    ]
    if not parameter_values:
        return {"injected": 0}
    await self._request(
        "InjectParameterDataRequest",
        {
            "faceFound": True,
            "mode": mode if mode in {"set", "add"} else "set",
            "parameterValues": parameter_values,
        },
    )
    return {"injected": len(parameter_values)}


async def _client_activate_expression(
    self: Any,
    expression_file: str,
    *,
    active: bool,
    fade_time: float = 0.25,
) -> dict[str, Any]:
    await self._request(
        "ExpressionActivationRequest",
        {
            "expressionFile": str(expression_file),
            "fadeTime": max(0.0, min(float(fade_time), 2.0)),
            "active": bool(active),
        },
    )
    return {"expression_file": expression_file, "active": bool(active)}


def install_vtube_motion_patch() -> None:
    if getattr(vtube.VTubeStudioSessionManager, "_aliver_motion_wizard_v1", False):
        return

    original_public_config = vtube.public_config
    original_runtime_status = vtube.VTubeStudioRuntime.status
    original_start = vtube.VTubeStudioSessionManager.start
    original_refresh = vtube.VTubeStudioSessionManager.refresh
    original_authorize = vtube.VTubeStudioSessionManager.authorize
    original_action = vtube.VTubeStudioSessionManager.action
    original_stop = vtube.VTubeStudioSessionManager.stop

    def patched_public_config(config: dict[str, Any]) -> dict[str, Any]:
        value = original_public_config(config)
        value["motion_engine"] = normalize_motion_engine(config.get("motion_engine"))
        return value

    def patched_runtime_status(self: Any) -> dict[str, Any]:
        value = original_runtime_status(self)
        value["motion_capabilities"] = self.state.get("motion_capabilities") or {}
        engine = getattr(self, "motion_engine", None)
        value["motion"] = engine.status() if engine is not None else {
            "enabled": False,
            "running": False,
            "current_mode": "disabled",
            "supported_actions": [],
        }
        return value

    async def attach_engine(runtime: Any, *, refresh: bool = False) -> None:
        capabilities = runtime.state.get("motion_capabilities")
        if refresh or not isinstance(capabilities, dict) or not capabilities:
            capabilities = await runtime.client.inspect_capabilities()
            runtime.state["motion_capabilities"] = capabilities
        engine = getattr(runtime, "motion_engine", None)
        if engine is None:
            engine = VTubeMotionEngine(
                runtime.client,
                runtime.config.get("motion_engine"),
                capabilities,
            )
            runtime.motion_engine = engine
        await engine.configure(runtime.config.get("motion_engine"), capabilities)

    async def patched_start(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
        await original_start(self, payload)
        session_id = str(payload.get("session_id") or "")
        runtime = self._runtime(session_id)
        try:
            await attach_engine(runtime, refresh=True)
            runtime.state["motion_error"] = None
        except Exception as exc:
            runtime.state["motion_error"] = f"{type(exc).__name__}: {exc}"
        return {
            **runtime.status(),
            "external_session_id": session_id,
            "message": "VTube Studio 已连接；自然动作配置向导已准备。",
        }

    async def patched_refresh(self: Any, session_id: str) -> dict[str, Any]:
        await original_refresh(self, session_id)
        runtime = self._runtime(session_id)
        try:
            await attach_engine(runtime, refresh=True)
            runtime.state["motion_error"] = None
        except Exception as exc:
            runtime.state["motion_error"] = f"{type(exc).__name__}: {exc}"
        return runtime.status()

    async def patched_authorize(self: Any, session_id: str) -> dict[str, Any]:
        await original_authorize(self, session_id)
        runtime = self._runtime(session_id)
        await attach_engine(runtime, refresh=True)
        return runtime.status()

    async def patched_action(
        self: Any,
        session_id: str,
        *,
        action: str | None = None,
        hotkey: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        action_name = str(action or "").strip().lower()
        identifier = str(hotkey or "").strip()
        if not identifier and action_name:
            identifier = str((runtime.config.get("hotkeys") or {}).get(action_name) or "").strip()

        engine = getattr(runtime, "motion_engine", None)
        procedural: dict[str, Any] | None = None
        if engine is not None and action_name:
            procedural = await engine.trigger(action_name)

        hotkey_result: dict[str, Any] | None = None
        if identifier:
            result = await original_action(
                self,
                session_id,
                action=action_name or None,
                hotkey=identifier,
                force=force,
            )
            hotkey_result = result.get("action_result")
        elif procedural is None:
            raise ValueError(
                f"No VTube Studio hotkey or procedural motion is available for action: "
                f"{action_name or 'unknown'}"
            )

        runtime.state["last_action"] = action_name or (hotkey_result or {}).get("hotkey_name")
        runtime.state["last_action_at"] = vtube._utc_now()
        return {
            **runtime.status(),
            "action_result": {
                "triggered": True,
                "action": action_name or None,
                "procedural": procedural,
                "hotkey": hotkey_result,
                "hotkey_name": (hotkey_result or {}).get("hotkey_name"),
                "hotkey_id": (hotkey_result or {}).get("hotkey_id"),
            },
        }

    async def patched_stop(self: Any, session_id: str) -> dict[str, Any]:
        runtime = self.sessions.get(session_id)
        engine = getattr(runtime, "motion_engine", None) if runtime is not None else None
        if engine is not None:
            await engine.stop(reset=True)
        return await original_stop(self, session_id)

    async def motion_scan(self: Any, session_id: str) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        capabilities = await runtime.client.inspect_capabilities()
        runtime.state["motion_capabilities"] = capabilities
        engine = getattr(runtime, "motion_engine", None)
        if engine is None:
            engine = VTubeMotionEngine(
                runtime.client,
                runtime.config.get("motion_engine"),
                capabilities,
            )
            runtime.motion_engine = engine
        else:
            await engine.configure(runtime.config.get("motion_engine"), capabilities)
        runtime.state["last_refresh_at"] = vtube._utc_now()
        return {
            "runtime": runtime.status(),
            "capabilities": capabilities,
            "recommended_config": capabilities.get("recommended_motion_engine") or {},
        }

    async def motion_configure(
        self: Any,
        session_id: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        merged = dict(runtime.config.get("motion_engine") or {})
        if isinstance(config, dict):
            merged.update(config)
        normalized = normalize_motion_engine(merged)
        runtime.config["motion_engine"] = normalized
        capabilities = runtime.state.get("motion_capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            capabilities = await runtime.client.inspect_capabilities()
            runtime.state["motion_capabilities"] = capabilities
        engine = getattr(runtime, "motion_engine", None)
        if engine is None:
            engine = VTubeMotionEngine(runtime.client, normalized, capabilities)
            runtime.motion_engine = engine
        await engine.configure(normalized, capabilities)
        runtime.state["last_refresh_at"] = vtube._utc_now()
        runtime.state["motion_error"] = None
        return runtime.status()

    vtube.public_config = patched_public_config
    vtube.VTubeStudioClient.input_parameters = _client_input_parameters
    vtube.VTubeStudioClient.live2d_parameters = _client_live2d_parameters
    vtube.VTubeStudioClient.expressions = _client_expressions
    vtube.VTubeStudioClient.inspect_capabilities = _client_inspect_capabilities
    vtube.VTubeStudioClient.parameter_value = _client_parameter_value
    vtube.VTubeStudioClient.inject_parameters = _client_inject_parameters
    vtube.VTubeStudioClient.activate_expression = _client_activate_expression
    vtube.VTubeStudioRuntime.status = patched_runtime_status
    vtube.VTubeStudioSessionManager.start = patched_start
    vtube.VTubeStudioSessionManager.refresh = patched_refresh
    vtube.VTubeStudioSessionManager.authorize = patched_authorize
    vtube.VTubeStudioSessionManager.action = patched_action
    vtube.VTubeStudioSessionManager.stop = patched_stop
    vtube.VTubeStudioSessionManager.motion_scan = motion_scan
    vtube.VTubeStudioSessionManager.motion_configure = motion_configure
    vtube.VTubeStudioSessionManager._aliver_motion_wizard_v1 = True
