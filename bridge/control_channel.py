from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from bridge.runtime_diagnostics import event, exception
from bridge.vtube_studio import PROVIDER_TYPES as VTUBE_STUDIO_PROVIDER_TYPES
from bridge.vtube_studio import VTubeStudioSessionManager


def install_bridge_control_guard(agent_module: Any) -> None:
    bridge_class = agent_module.BridgeAgent
    if getattr(bridge_class, "_aliver_control_guard_v1", False):
        return

    original_init = bridge_class.__init__
    original_capabilities = bridge_class.capabilities
    original_execute = bridge_class.execute
    original_system_info = bridge_class.system_info

    def patched_init(self) -> None:
        original_init(self)
        self._control_send_lock = asyncio.Lock()
        self._control_connection_generation = 0
        self.vtube_studio = VTubeStudioSessionManager()

    def patched_capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in (
            "provider.vtube_studio.local",
            "provider.vtube_studio.model",
            "provider.vtube_studio.hotkeys",
            "provider.vtube_studio.actions",
            "provider.vtube_studio.authorization",
            "provider.avatar.active_session_metadata",
        ):
            if item not in values:
                values.append(item)
        return values

    def patched_system_info(self) -> dict[str, Any]:
        value = dict(original_system_info(self))
        vtube_sessions = self.vtube_studio.status()
        value["vtube_studio_sessions"] = vtube_sessions
        avatar_sessions: dict[str, Any] = {}
        simli_sessions = value.get("simli_sessions")
        if isinstance(simli_sessions, dict):
            avatar_sessions.update(simli_sessions)
        if isinstance(vtube_sessions, dict):
            avatar_sessions.update(vtube_sessions)
        value["avatar_sessions"] = avatar_sessions
        return value

    async def patched_execute(
        self,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        provider_type = str(payload.get("provider_type") or "")
        if command_type == "provider.start_session" and provider_type in VTUBE_STUDIO_PROVIDER_TYPES:
            return await self.vtube_studio.start(payload)
        if command_type == "provider.stop_session" and provider_type in VTUBE_STUDIO_PROVIDER_TYPES:
            return await self.vtube_studio.stop(str(payload.get("session_id") or ""))
        if command_type == "provider.vtube_studio.status":
            return self.vtube_studio.status(
                str(payload.get("session_id") or "").strip() or None
            )
        if command_type == "provider.vtube_studio.refresh":
            return await self.vtube_studio.refresh(str(payload.get("session_id") or ""))
        if command_type == "provider.vtube_studio.authorize":
            return await self.vtube_studio.authorize(str(payload.get("session_id") or ""))
        if command_type == "provider.vtube_studio.action":
            return await self.vtube_studio.action(
                str(payload.get("session_id") or ""),
                action=str(payload.get("action") or "").strip() or None,
                hotkey=str(payload.get("hotkey") or "").strip() or None,
                force=bool(payload.get("force", False)),
            )
        return await original_execute(self, command_type, payload)

    async def safe_send(self, ws, payload: dict[str, Any]) -> bool:
        try:
            async with self._control_send_lock:
                await asyncio.wait_for(
                    ws.send(json.dumps(payload, ensure_ascii=False)),
                    timeout=5.0,
                )
            return True
        except (websockets.ConnectionClosed, TimeoutError, OSError) as exc:
            event(
                "bridge_control_send_dropped",
                error=f"{type(exc).__name__}: {exc}",
                payload_type=payload.get("type"),
                command_id=payload.get("command_id"),
            )
            return False

    async def patched_heartbeat_loop(self, ws) -> None:
        configured = float(self.config.get("heartbeat_seconds", 10))
        # Reconciliation uses this heartbeat. Keep it frequent on the local control plane
        # so a closed/failed avatar disappears from the server UI within a few seconds.
        interval = min(3.0, max(1.0, configured))
        while True:
            sent = await self._control_safe_send(
                ws,
                {"type": "heartbeat", "metadata": self.system_info()},
            )
            if not sent:
                return
            await asyncio.sleep(interval)

    async def patched_handle_command(self, ws, message: dict[str, Any]) -> None:
        command_id = str(message.get("command_id", ""))
        command_type = str(message.get("command_type", ""))
        payload = message.get("payload") or {}
        try:
            data = await self.execute(command_type, payload)
            response = {
                "type": "result",
                "command_id": command_id,
                "ok": True,
                "data": data,
            }
        except asyncio.CancelledError:
            event(
                "bridge_control_command_cancelled",
                command_id=command_id,
                command_type=command_type,
            )
            raise
        except Exception as exc:
            response = {
                "type": "result",
                "command_id": command_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        await self._control_safe_send(ws, response)

    async def patched_run(self) -> None:
        try:
            await self.sync_registration()
            while not self.stop_event.is_set():
                connection_tasks: set[asyncio.Task] = set()
                heartbeat: asyncio.Task | None = None
                try:
                    async with websockets.connect(
                        self.ws_url(),
                        ping_interval=10,
                        ping_timeout=45,
                        close_timeout=5,
                        max_queue=64,
                    ) as ws:
                        self._control_connection_generation += 1
                        generation = self._control_connection_generation
                        print("Bridge connected to ALiver")
                        event("bridge_control_connected", generation=generation)
                        heartbeat = asyncio.create_task(
                            self.heartbeat_loop(ws),
                            name=f"bridge-control-heartbeat-{generation}",
                        )
                        try:
                            async for raw in ws:
                                message = json.loads(raw)
                                if message.get("type") != "command":
                                    continue
                                task = asyncio.create_task(
                                    self.handle_command(ws, message),
                                    name=f"bridge-command-{message.get('command_id', 'unknown')}",
                                )
                                connection_tasks.add(task)
                                task.add_done_callback(connection_tasks.discard)
                        finally:
                            if heartbeat is not None:
                                heartbeat.cancel()
                                await asyncio.gather(heartbeat, return_exceptions=True)
                            pending = [task for task in connection_tasks if not task.done()]
                            for task in pending:
                                task.cancel()
                            if pending:
                                await asyncio.gather(*pending, return_exceptions=True)
                            event(
                                "bridge_control_connection_cleanup",
                                generation=generation,
                                cancelled_commands=len(pending),
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    exception("bridge_control_disconnected", exc)
                    print(f"Bridge disconnected: {exc}. Reconnecting in 3 seconds...")
                    await asyncio.sleep(3)
        finally:
            await self.vtube_studio.stop_all()

    bridge_class.__init__ = patched_init
    bridge_class.capabilities = staticmethod(patched_capabilities)
    bridge_class.system_info = patched_system_info
    bridge_class.execute = patched_execute
    bridge_class._control_safe_send = safe_send
    bridge_class.heartbeat_loop = patched_heartbeat_loop
    bridge_class.handle_command = patched_handle_command
    bridge_class.run = patched_run
    bridge_class._aliver_control_guard_v1 = True
