from __future__ import annotations

import time
from typing import Any

from app.vtube_motion_config import normalize_motion_engine
from bridge import vtube_motion, vtube_studio


def _supported_actions(roles: dict[str, str]) -> list[str]:
    result = ["reset"]
    natural = any(
        role in roles
        for role in ("angle_x", "angle_y", "angle_z", "position_x", "position_y")
    )
    if natural:
        result[0:0] = ["idle", "talking"]
    if any(role in roles for role in ("angle_y", "angle_z", "eye_y_left", "eye_y_right")):
        result.append("thinking")
    if any(role in roles for role in ("angle_x", "angle_z", "position_y")):
        result.append("wave")
    if natural or "mouth_smile" in roles:
        result.append("happy")
    if any(
        role in roles
        for role in (
            "angle_y",
            "position_y",
            "eye_open_left",
            "eye_open_right",
            "brow_y_left",
            "brow_y_right",
        )
    ):
        result.append("surprised")
    return list(dict.fromkeys(result))


def install_vtube_motion_safety_patch() -> None:
    manager_class = vtube_studio.VTubeStudioSessionManager
    if getattr(manager_class, "_aliver_motion_safety_v1", False):
        return

    original_build = vtube_motion.build_motion_capabilities
    original_configure = manager_class.motion_configure

    def build_motion_capabilities(
        input_payload: dict[str, Any],
        live2d_payload: dict[str, Any],
        expression_payload: dict[str, Any],
    ) -> dict[str, Any]:
        value = original_build(input_payload, live2d_payload, expression_payload)
        roles = value.get("role_map")
        value["supported_actions"] = _supported_actions(
            dict(roles) if isinstance(roles, dict) else {}
        )
        return value

    async def motion_configure(
        self: Any,
        session_id: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        capabilities = runtime.state.get("motion_capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            capabilities = await runtime.client.inspect_capabilities()
            runtime.state["motion_capabilities"] = capabilities

        merged = dict(runtime.config.get("motion_engine") or {})
        if isinstance(config, dict):
            merged.update(config)
        normalized = normalize_motion_engine(merged)
        recommended = capabilities.get("recommended_motion_engine")
        recommended_map = (
            recommended.get("expression_map")
            if isinstance(recommended, dict)
            else None
        )
        if isinstance(recommended_map, dict):
            expression_map = dict(normalized.get("expression_map") or {})
            for action, expression_file in recommended_map.items():
                if expression_file and not expression_map.get(action):
                    expression_map[action] = str(expression_file)
            normalized["expression_map"] = expression_map

        return await original_configure(self, session_id, normalized)

    async def action(
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
            identifier = str(
                (runtime.config.get("hotkeys") or {}).get(action_name) or ""
            ).strip()

        engine = getattr(runtime, "motion_engine", None)
        supported = set(
            (engine.status().get("supported_actions") if engine is not None else []) or []
        )
        procedural: dict[str, Any] | None = None
        if engine is not None and action_name in supported:
            procedural = await engine.trigger(action_name)

        hotkey_result: dict[str, Any] | None = None
        if identifier:
            now = time.monotonic()
            cooldown = max(
                0,
                int(runtime.config.get("action_cooldown_ms", 1200)),
            ) / 1000
            if force or now - runtime.last_action_at >= cooldown:
                hotkey_result = await runtime.client.trigger_hotkey(identifier)
                runtime.last_action_at = now

        if procedural is None and hotkey_result is None:
            raise ValueError(
                "The current model has no procedural parameter support or configured hotkey "
                f"for action: {action_name or 'unknown'}"
            )

        runtime.state["last_action"] = action_name or (hotkey_result or {}).get(
            "hotkey_name"
        )
        runtime.state["last_action_at"] = vtube_studio._utc_now()
        runtime.state["last_refresh_at"] = vtube_studio._utc_now()
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

    vtube_motion.build_motion_capabilities = build_motion_capabilities
    manager_class.motion_configure = motion_configure
    manager_class.action = action
    manager_class._aliver_motion_safety_v1 = True
