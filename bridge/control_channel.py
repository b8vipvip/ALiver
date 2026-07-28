from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from bridge.runtime_diagnostics import event, exception


def install_bridge_control_guard(agent_module: Any) -> None:
    bridge_class = agent_module.BridgeAgent
    if getattr(bridge_class, "_aliver_control_guard_v1", False):
        return

    original_init = bridge_class.__init__

    def patched_init(self) -> None:
        original_init(self)
        self._control_send_lock = asyncio.Lock()
        self._control_connection_generation = 0

    async def safe_send(self, ws, payload: dict[str, Any]) -> bool:
        try:
            async with self._control_send_lock:
                await ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except websockets.ConnectionClosed as exc:
            event(
                "bridge_control_send_dropped",
                error=f"{type(exc).__name__}: {exc}",
                payload_type=payload.get("type"),
                command_id=payload.get("command_id"),
            )
            return False

    async def patched_heartbeat_loop(self, ws) -> None:
        interval = max(3.0, float(self.config.get("heartbeat_seconds", 10)))
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

    bridge_class.__init__ = patched_init
    bridge_class._control_safe_send = safe_send
    bridge_class.heartbeat_loop = patched_heartbeat_loop
    bridge_class.handle_command = patched_handle_command
    bridge_class.run = patched_run
    bridge_class._aliver_control_guard_v1 = True
