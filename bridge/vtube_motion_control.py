from __future__ import annotations

from typing import Any

from bridge import control_channel


def install_vtube_motion_control_patch() -> None:
    if getattr(control_channel, "_aliver_vtube_motion_control_v1", False):
        return

    original_install = control_channel.install_bridge_control_guard

    def install(agent_module: Any) -> None:
        original_install(agent_module)
        bridge_class = agent_module.BridgeAgent
        if getattr(bridge_class, "_aliver_vtube_motion_control_v1", False):
            return

        original_capabilities = bridge_class.capabilities
        original_execute = bridge_class.execute

        def capabilities() -> list[str]:
            values = list(original_capabilities())
            for item in (
                "provider.vtube_studio.motion.scan",
                "provider.vtube_studio.motion.configure",
                "provider.vtube_studio.motion.auto_speech",
                "provider.vtube_studio.motion.expressions",
                "provider.vtube_studio.motion.procedural_actions",
            ):
                if item not in values:
                    values.append(item)
            return values

        async def execute(
            self: Any,
            command_type: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            session_id = str(payload.get("session_id") or "")
            if command_type == "provider.vtube_studio.motion.scan":
                return await self.vtube_studio.motion_scan(session_id)
            if command_type == "provider.vtube_studio.motion.configure":
                config = payload.get("motion_engine")
                if config is not None and not isinstance(config, dict):
                    raise ValueError("motion_engine must be a JSON object")
                return await self.vtube_studio.motion_configure(session_id, config)
            return await original_execute(self, command_type, payload)

        bridge_class.capabilities = staticmethod(capabilities)
        bridge_class.execute = execute
        bridge_class._aliver_vtube_motion_control_v1 = True

    control_channel.install_bridge_control_guard = install
    control_channel._aliver_vtube_motion_control_v1 = True
