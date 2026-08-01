from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("aliver")


def is_harmless_windows_reset(context: dict[str, Any]) -> bool:
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    if getattr(exc, "winerror", None) != 10054:
        return False
    message = str(context.get("message") or "")
    handle = str(context.get("handle") or "")
    return "_call_connection_lost" in message or "_call_connection_lost" in handle


def install_windows_asyncio_noise_filter(application: Any) -> None:
    if getattr(application.state, "windows_asyncio_noise_filter", False):
        return
    application.state.windows_asyncio_noise_filter = True

    @application.on_event("startup")
    async def _install_handler() -> None:
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()

        def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            if is_harmless_windows_reset(context):
                logger.debug("Ignored closed WebSocket transport reset: %s", context.get("exception"))
                return
            if previous is not None:
                previous(current_loop, context)
            else:
                current_loop.default_exception_handler(context)

        loop.set_exception_handler(handler)
