from __future__ import annotations

import asyncio
from typing import Any

import httpx

from bridge.runtime_diagnostics import event, exception

BRIDGE_PATCH_VERSION = "0.12.2"


def install_bridge_startup_retry_patch() -> None:
    from bridge import agent

    bridge_class = agent.BridgeAgent
    if getattr(bridge_class, "_aliver_startup_retry_patch", False):
        return
    original = bridge_class.sync_registration

    async def sync_registration(self: Any) -> None:
        delay = 2.0
        attempt = 0
        while not self.stop_event.is_set():
            attempt += 1
            agent.BRIDGE_VERSION = BRIDGE_PATCH_VERSION
            try:
                await original(self)
                if attempt > 1:
                    print("ALiver 服务端已恢复，Bridge 注册成功。")
                    event("bridge_startup_registration_recovered", attempt=attempt)
                return
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                exception("bridge_startup_registration_wait", exc)
                print(
                    "暂时无法连接 ALiver 服务端 "
                    f"{self.server_url}：{type(exc).__name__}: {exc}。"
                    f"将在 {delay:.0f} 秒后重试，Bridge 不会退出。"
                )
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(delay * 1.6, 15.0)
        raise asyncio.CancelledError

    bridge_class.sync_registration = sync_registration
    bridge_class._aliver_startup_retry_patch = True
