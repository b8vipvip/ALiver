from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(slots=True)
class ExtensionConnection:
    websocket: WebSocket
    lock: asyncio.Lock


class ExtensionHub:
    def __init__(self) -> None:
        self._connections: dict[str, ExtensionConnection] = {}
        self._guard = asyncio.Lock()

    async def connect(self, extension_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._guard:
            old = self._connections.pop(extension_id, None)
            if old:
                try:
                    await old.websocket.close(code=1012)
                except Exception:
                    pass
            self._connections[extension_id] = ExtensionConnection(websocket, asyncio.Lock())

    async def disconnect(self, extension_id: str) -> None:
        async with self._guard:
            self._connections.pop(extension_id, None)

    def is_connected(self, extension_id: str) -> bool:
        return extension_id in self._connections

    async def send_command(
        self,
        extension_id: str,
        *,
        command_id: str,
        command_type: str,
        payload: dict,
    ) -> None:
        connection = self._connections.get(extension_id)
        if not connection:
            raise RuntimeError("Chrome extension is not connected")
        message = {
            "type": "director.command",
            "command_id": command_id,
            "command_type": command_type,
            "payload": payload,
        }
        async with connection.lock:
            await connection.websocket.send_json(message)


extension_hub = ExtensionHub()
