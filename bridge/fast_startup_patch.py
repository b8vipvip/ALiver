from __future__ import annotations

import os
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx


def _fast_platform_summary() -> str:
    if os.name == "nt":
        try:
            value = sys.getwindowsversion()
            return f"Windows-{value.major}.{value.minor}.{value.build}"
        except Exception:
            return "Windows"
    return sys.platform


def _is_local_server(url: str) -> bool:
    host = (urlparse(url).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _client(server_url: str) -> httpx.AsyncClient:
    local = _is_local_server(server_url)
    timeout = httpx.Timeout(
        connect=3.0 if local else 10.0,
        read=12.0 if local else 20.0,
        write=12.0 if local else 20.0,
        pool=5.0,
    )
    # Windows proxy auto-discovery and inherited HTTP(S)_PROXY values can add
    # tens of seconds even for 127.0.0.1. Local ALiver traffic must never use
    # an external proxy.
    return httpx.AsyncClient(timeout=timeout, trust_env=not local)


def install_bridge_fast_startup_patch() -> None:
    from bridge import agent

    bridge_class = agent.BridgeAgent
    if getattr(bridge_class, "_aliver_fast_startup_patch", False):
        return

    original_init = bridge_class.__init__

    def patched_init(self: Any) -> None:
        started = time.monotonic()
        original_init(self)
        self._aliver_static_system_info = {
            "platform": _fast_platform_summary(),
            "python": sys.version.split()[0],
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
        }
        self._aliver_core_init_ms = round((time.monotonic() - started) * 1000, 1)
        print(f"Bridge 核心初始化完成：{self._aliver_core_init_ms:.1f} ms")

    def patched_system_info(self: Any) -> dict[str, Any]:
        value = dict(getattr(self, "_aliver_static_system_info", {}))
        value.update(
            {
                "bridge_version": agent.BRIDGE_VERSION,
                "audio_capture": self.audio.status(),
                "core_init_ms": getattr(self, "_aliver_core_init_ms", None),
            }
        )
        return value

    async def patched_register(self: Any) -> None:
        payload = {
            "name": self.config.get("name", "Windows AI Live Bridge"),
            "machine_name": socket.gethostname(),
            "version": agent.BRIDGE_VERSION,
            "capabilities": self.capabilities(),
            "metadata": self.system_info(),
        }
        async with _client(self.server_url) as client:
            response = await client.post(f"{self.server_url}/api/bridges/register", json=payload)
            response.raise_for_status()
            self.state = response.json()
            self.save_state()
        print(f"Registered Bridge: {self.state['bridge_id']}")

    async def patched_sync_registration(self: Any) -> None:
        bridge_id = self.state.get("bridge_id")
        token = self.state.get("token")
        if not bridge_id or not token:
            await self.register()
            return
        payload = {
            "version": agent.BRIDGE_VERSION,
            "capabilities": self.capabilities(),
            "metadata": self.system_info(),
        }
        headers = {"X-Bridge-Token": str(token)}
        async with _client(self.server_url) as client:
            response = await client.post(
                f"{self.server_url}/api/bridges/{bridge_id}/heartbeat",
                json=payload,
                headers=headers,
            )
            if response.status_code in (401, 404):
                self.state = {}
                await self.register()
                return
            response.raise_for_status()

    bridge_class.__init__ = patched_init
    bridge_class.system_info = patched_system_info
    bridge_class.register = patched_register
    bridge_class.sync_registration = patched_sync_registration
    bridge_class._aliver_fast_startup_patch = True
