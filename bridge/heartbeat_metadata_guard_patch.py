from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from typing import Any

from bridge.runtime_diagnostics import event, exception

METADATA_WAIT_SECONDS = 1.5


def _fallback_metadata(instance: Any) -> dict[str, Any]:
    value = dict(getattr(instance, "_control_metadata_cache", {}) or {})
    static = dict(getattr(instance, "_aliver_static_system_info", {}) or {})
    for key, fallback in (
        ("platform", sys.platform),
        ("python", sys.version.split()[0]),
        ("hostname", socket.gethostname()),
        ("pid", os.getpid()),
    ):
        value.setdefault(key, static.get(key) or fallback)
    value.setdefault("bridge_version", getattr(sys.modules.get("bridge.agent"), "BRIDGE_VERSION", None))
    value["metadata_phase"] = "heartbeat_cached"
    value["metadata_refresh_pending"] = True
    return value


def install_heartbeat_metadata_guard_patch(agent_module: Any) -> None:
    bridge_class = agent_module.BridgeAgent
    if getattr(bridge_class, "_aliver_heartbeat_metadata_guard", False):
        return

    async def metadata_for_heartbeat(self: Any) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        future = getattr(self, "_control_metadata_future", None)
        if future is None:
            future = loop.run_in_executor(None, self.system_info)
            self._control_metadata_future = future

        try:
            value = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=METADATA_WAIT_SECONDS,
            )
        except TimeoutError:
            now = time.monotonic()
            previous = float(getattr(self, "_control_metadata_timeout_logged_at", 0.0) or 0.0)
            if now - previous >= 30.0:
                self._control_metadata_timeout_logged_at = now
                event(
                    "bridge_heartbeat_metadata_deferred",
                    timeout_seconds=METADATA_WAIT_SECONDS,
                )
            return _fallback_metadata(self)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._control_metadata_future = None
            exception("bridge_heartbeat_metadata_failed", exc)
            return _fallback_metadata(self)

        self._control_metadata_future = None
        if not isinstance(value, dict):
            value = _fallback_metadata(self)
        else:
            value = dict(value)
            value["metadata_phase"] = "heartbeat_full"
            value["metadata_refresh_pending"] = False
            self._control_metadata_cache = dict(value)
        return value

    async def patched_heartbeat_loop(self: Any, ws: Any) -> None:
        configured = float(self.config.get("heartbeat_seconds", 10))
        interval = min(3.0, max(1.0, configured))
        while True:
            metadata = await metadata_for_heartbeat(self)
            sent = await self._control_safe_send(
                ws,
                {"type": "heartbeat", "metadata": metadata},
            )
            if not sent:
                return
            await asyncio.sleep(interval)

    bridge_class._aliver_metadata_for_heartbeat = metadata_for_heartbeat
    bridge_class.heartbeat_loop = patched_heartbeat_loop
    bridge_class._aliver_heartbeat_metadata_guard = True
