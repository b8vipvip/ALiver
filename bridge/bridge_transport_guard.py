from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from bridge.runtime_diagnostics import event, exception


async def _send_json_safely(ws: Any, payload: dict[str, Any], *, command_id: str = "") -> bool:
    try:
        await asyncio.wait_for(
            ws.send(json.dumps(payload, ensure_ascii=False)),
            timeout=5.0,
        )
        return True
    except (ConnectionClosed, TimeoutError, OSError) as exc:
        event(
            "bridge_ws_response_dropped",
            command_id=command_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False
    except Exception as exc:
        exception("bridge_ws_response_failed", exc, command_id=command_id)
        return False


def _consume_task(task: asyncio.Task, tasks: set[asyncio.Task]) -> None:
    tasks.discard(task)
    if task.cancelled():
        return
    with suppress(Exception):
        task.result()


def install_bridge_transport_guard(agent_class: type) -> None:
    if getattr(agent_class, "_aliver_transport_guard_v1", False):
        return

    async def guarded_handle_command(self, ws: Any, message: dict[str, Any]) -> None:
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
                "bridge_command_cancelled_on_disconnect",
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
        await _send_json_safely(ws, response, command_id=command_id)

    async def guarded_heartbeat_loop(self, ws: Any) -> None:
        interval = max(3.0, float(self.config.get("heartbeat_seconds", 10)))
        while True:
            payload = {"type": "heartbeat", "metadata": self.system_info()}
            if not await _send_json_safely(ws, payload):
                return
            await asyncio.sleep(interval)

    async def guarded_run(self) -> None:
        await self.sync_registration()
        reconnect_delay = 1.0
        while not self.stop_event.is_set():
            heartbeat: asyncio.Task | None = None
            command_tasks: set[asyncio.Task] = set()
            try:
                async with websockets.connect(
                    self.ws_url(),
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=5,
                    open_timeout=20,
                    max_queue=64,
                ) as ws:
                    reconnect_delay = 1.0
                    print("Bridge connected to ALiver")
                    event("bridge_ws_connected")
                    heartbeat = asyncio.create_task(
                        guarded_heartbeat_loop(self, ws),
                        name="bridge-control-heartbeat",
                    )
                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("type") != "command":
                            continue
                        command_type = str(message.get("command_type") or "unknown")
                        command_id = str(message.get("command_id") or "")
                        task = asyncio.create_task(
                            guarded_handle_command(self, ws, message),
                            name=f"bridge-command:{command_type}:{command_id}",
                        )
                        command_tasks.add(task)
                        task.add_done_callback(lambda item, owned=command_tasks: _consume_task(item, owned))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"Bridge disconnected: {exc}. "
                    f"Reconnecting in {reconnect_delay:.0f} seconds..."
                )
                event(
                    "bridge_ws_disconnected",
                    error=f"{type(exc).__name__}: {exc}",
                    reconnect_delay_seconds=reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(8.0, reconnect_delay * 1.8)
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                if command_tasks:
                    for task in list(command_tasks):
                        task.cancel()
                    await asyncio.gather(*command_tasks, return_exceptions=True)
                    command_tasks.clear()

    agent_class.run = guarded_run
    agent_class.heartbeat_loop = guarded_heartbeat_loop
    agent_class.handle_command = guarded_handle_command
    agent_class._aliver_transport_guard_v1 = True
