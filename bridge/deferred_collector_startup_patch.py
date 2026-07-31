from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets

from bridge.runtime_diagnostics import event, exception


def install_deferred_collector_startup_patch(agent_module: Any) -> None:
    bridge_class = agent_module.BridgeAgent
    if getattr(bridge_class, "_aliver_deferred_collector_startup", False):
        return

    async def warm_collector(self: Any) -> None:
        delay = float(self.config.get("collector_autostart_delay_seconds", 3.0) or 0.0)
        delay = max(0.0, min(delay, 30.0))
        if delay:
            await asyncio.sleep(delay)
        started = time.monotonic()
        event("bridge_collector_autostart_begin", delay_seconds=delay)
        try:
            status = await asyncio.to_thread(self.douyin_collector.autostart)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            event(
                "bridge_collector_autostart_ready",
                elapsed_ms=elapsed_ms,
                status=str((status or {}).get("status") or "unknown"),
            )
            print(f"互动采集器后台初始化完成：{elapsed_ms:.1f} ms")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            exception("bridge_collector_autostart_failed", exc)
            print(f"互动采集器自动启动失败：{type(exc).__name__}: {exc}")

    async def patched_run(self: Any) -> None:
        collector_task: asyncio.Task | None = None
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

                        # Do not block registration/WebSocket readiness on OCR,
                        # UIA or ONNX warmup. Start the collector only after the
                        # server already sees the Bridge online.
                        if collector_task is None or collector_task.done():
                            collector_task = asyncio.create_task(
                                warm_collector(self),
                                name="bridge-collector-deferred-autostart",
                            )

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
            if collector_task is not None and not collector_task.done():
                collector_task.cancel()
                await asyncio.gather(collector_task, return_exceptions=True)
            await asyncio.to_thread(self.douyin_collector.stop)
            await self.vtube_studio.stop_all()

    bridge_class.run = patched_run
    bridge_class._aliver_deferred_collector_startup = True
