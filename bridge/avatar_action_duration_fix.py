from __future__ import annotations

from typing import Any

from bridge import vtube_motion


def install_avatar_action_duration_fix() -> None:
    engine_class = vtube_motion.VTubeMotionEngine
    if getattr(engine_class, "_aliver_avatar_action_duration_v1", False):
        return

    original_init = engine_class.__init__

    def patched_init(
        self: Any,
        client: Any,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None,
    ) -> None:
        original_init(self, client, config, capabilities)
        router = getattr(self, "action_router", None)
        if router is None:
            return

        async def routed_trigger(action: str) -> dict[str, Any]:
            result = await self.trigger(action)
            active_until = float(getattr(router, "_active_until", 0.0))
            active_started = float(getattr(router, "_active_started", 0.0))
            if action not in {"idle", "reset"} and active_until > 0:
                self._transient_action = action
                self._transient_started = active_started
                self._transient_until = active_until
            return result

        router._trigger = routed_trigger

    engine_class.__init__ = patched_init
    engine_class._aliver_avatar_action_duration_v1 = True
