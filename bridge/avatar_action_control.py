from __future__ import annotations

from typing import Any

from bridge import control_channel, vtube_motion, vtube_studio as vtube
from bridge.avatar_action_router import AvatarActionRouter


def install_avatar_action_control_patch() -> None:
    engine_class = vtube_motion.VTubeMotionEngine
    if getattr(engine_class, "_aliver_avatar_action_router_v1", False):
        return

    original_init = engine_class.__init__
    original_poll_voice = engine_class._poll_voice
    original_status = engine_class.status
    original_configure = engine_class.configure
    original_stop = engine_class.stop

    def patched_init(
        self: Any,
        client: Any,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None,
    ) -> None:
        original_init(self, client, config, capabilities)

        async def clear_transient() -> None:
            self._transient_action = None
            self._transient_started = 0.0
            self._transient_until = 0.0
            if getattr(self, "_active_expression", None):
                await self._deactivate_expression()

        self.action_router = AvatarActionRouter(
            trigger=self.trigger,
            clear_transient=clear_transient,
            reset=self.reset,
            cooldown_ms=int(self.config.get("action_cooldown_ms", 1200)),
        )

    async def patched_poll_voice(self: Any, now: float) -> None:
        await original_poll_voice(self, now)
        router = getattr(self, "action_router", None)
        if router is not None:
            await router.sync_speech(bool(getattr(self, "_speaking", False)))

    def patched_status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        router = getattr(self, "action_router", None)
        value["action_router"] = router.status() if router is not None else {
            "base_mode": value.get("current_mode", "idle"),
            "speaking": bool(value.get("speaking")),
            "active": None,
            "queue_count": 0,
            "queue": [],
            "next_state": value.get("current_mode", "idle"),
            "history": [],
        }
        return value

    async def patched_configure(
        self: Any,
        config: dict[str, Any] | None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = await original_configure(self, config, capabilities)
        router = getattr(self, "action_router", None)
        if router is not None:
            router.cooldown_ms = max(
                0,
                int(
                    (config or {}).get(
                        "action_cooldown_ms",
                        self.config.get("action_cooldown_ms", router.cooldown_ms),
                    )
                ),
            )
        return value

    async def patched_stop(self: Any, *, reset: bool = True) -> None:
        router = getattr(self, "action_router", None)
        if router is not None:
            await router.clear(reason="engine_stop")
        await original_stop(self, reset=reset)

    engine_class.__init__ = patched_init
    engine_class._poll_voice = patched_poll_voice
    engine_class.status = patched_status
    engine_class.configure = patched_configure
    engine_class.stop = patched_stop
    engine_class._aliver_avatar_action_router_v1 = True

    manager_class = vtube.VTubeStudioSessionManager
    if not getattr(manager_class, "_aliver_avatar_action_router_v1", False):

        async def action_route(
            self: Any,
            session_id: str,
            *,
            action: str,
            source: str = "system",
            priority: int | None = None,
            duration_ms: int | None = None,
            interrupt: bool = True,
            force: bool = False,
            correlation_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            runtime = self._runtime(session_id)
            engine = getattr(runtime, "motion_engine", None)
            if engine is None:
                raise RuntimeError("VTube Studio natural-motion engine is not initialized")
            router = getattr(engine, "action_router", None)
            if router is None:
                raise RuntimeError("VTube Studio action router is not initialized")
            routed = await router.submit(
                action,
                source=source,
                priority=priority,
                duration_ms=duration_ms,
                interrupt=interrupt,
                force=force,
                correlation_id=correlation_id,
                metadata=metadata,
            )
            runtime.state["last_action"] = action
            runtime.state["last_action_at"] = vtube._utc_now()
            runtime.state["last_action_source"] = source
            return {**runtime.status(), "route_result": routed}

        async def action_router_status(self: Any, session_id: str) -> dict[str, Any]:
            runtime = self._runtime(session_id)
            engine = getattr(runtime, "motion_engine", None)
            router = getattr(engine, "action_router", None) if engine is not None else None
            return {
                "session_id": session_id,
                "status": router.status() if router is not None else None,
                "runtime": runtime.status(),
            }

        async def action_router_clear(
            self: Any,
            session_id: str,
            *,
            source: str | None = None,
            include_active: bool = True,
        ) -> dict[str, Any]:
            runtime = self._runtime(session_id)
            engine = getattr(runtime, "motion_engine", None)
            router = getattr(engine, "action_router", None) if engine is not None else None
            if router is None:
                raise RuntimeError("VTube Studio action router is not initialized")
            status = await router.clear(
                source=source,
                include_active=include_active,
                reason="api_clear",
            )
            return {**runtime.status(), "route_result": {"cleared": True, "status": status}}

        manager_class.action_route = action_route
        manager_class.action_router_status = action_router_status
        manager_class.action_router_clear = action_router_clear
        manager_class._aliver_avatar_action_router_v1 = True

    if getattr(control_channel, "_aliver_avatar_action_router_v1", False):
        return

    original_install = control_channel.install_bridge_control_guard

    def install(agent_module: Any) -> None:
        original_install(agent_module)
        bridge_class = agent_module.BridgeAgent
        if getattr(bridge_class, "_aliver_avatar_action_router_v1", False):
            return

        original_capabilities = bridge_class.capabilities
        original_execute = bridge_class.execute

        def capabilities() -> list[str]:
            values = list(original_capabilities())
            for item in (
                "provider.vtube_studio.action.route",
                "provider.vtube_studio.action.router_status",
                "provider.vtube_studio.action.queue_clear",
                "provider.vtube_studio.action.priority_queue",
                "provider.vtube_studio.action.cooldown",
                "provider.vtube_studio.action.timeout_restore",
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
            if command_type == "provider.vtube_studio.action.route":
                return await self.vtube_studio.action_route(
                    session_id,
                    action=str(payload.get("action") or ""),
                    source=str(payload.get("source") or "system"),
                    priority=(int(payload["priority"]) if payload.get("priority") is not None else None),
                    duration_ms=(
                        int(payload["duration_ms"])
                        if payload.get("duration_ms") is not None
                        else None
                    ),
                    interrupt=bool(payload.get("interrupt", True)),
                    force=bool(payload.get("force", False)),
                    correlation_id=(
                        str(payload.get("correlation_id"))
                        if payload.get("correlation_id")
                        else None
                    ),
                    metadata=(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None),
                )
            if command_type == "provider.vtube_studio.action.router_status":
                return await self.vtube_studio.action_router_status(session_id)
            if command_type == "provider.vtube_studio.action.queue_clear":
                return await self.vtube_studio.action_router_clear(
                    session_id,
                    source=(str(payload.get("source")) if payload.get("source") else None),
                    include_active=bool(payload.get("include_active", True)),
                )
            return await original_execute(self, command_type, payload)

        bridge_class.capabilities = staticmethod(capabilities)
        bridge_class.execute = execute
        bridge_class._aliver_avatar_action_router_v1 = True

    control_channel.install_bridge_control_guard = install
    control_channel._aliver_avatar_action_router_v1 = True
