from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
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
    """Install the narrow WinError 10054 filter on Uvicorn's real event loop.

    ALiver uses FastAPI's lifespan context. When a lifespan context is supplied,
    Starlette does not execute handlers registered with ``on_event('startup')``.
    Therefore this patch wraps the existing lifespan instead of registering a
    startup callback, ensuring the handler is installed on the loop that
    actually owns the Proactor transports.
    """
    if getattr(application.state, "windows_asyncio_noise_filter", False):
        return

    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan_with_windows_reset_filter(app: Any):
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()

        def handler(
            current_loop: asyncio.AbstractEventLoop,
            context: dict[str, Any],
        ) -> None:
            if is_harmless_windows_reset(context):
                logger.debug(
                    "Ignored closed WebSocket transport reset: %s",
                    context.get("exception"),
                )
                return
            if previous is not None:
                previous(current_loop, context)
            else:
                current_loop.default_exception_handler(context)

        loop.set_exception_handler(handler)
        application.state.windows_asyncio_noise_filter_active = True
        try:
            async with original_lifespan(app) as state:
                yield state
        finally:
            application.state.windows_asyncio_noise_filter_active = False
            if loop.get_exception_handler() is handler:
                loop.set_exception_handler(previous)

    application.router.lifespan_context = lifespan_with_windows_reset_filter
    application.state.windows_asyncio_noise_filter = True
