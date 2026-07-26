from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from fastapi import WebSocket


@dataclass(slots=True)
class BridgeConnection:
    websocket: WebSocket
    lock: asyncio.Lock


class BridgeHub:
    def __init__(self) -> None:
        self._connections: dict[str, BridgeConnection] = {}
        self._pending: dict[str, asyncio.Future] = {}
        self._guard = asyncio.Lock()

    async def connect(self, bridge_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._guard:
            old = self._connections.pop(bridge_id, None)
            if old:
                try:
                    await old.websocket.close(code=1012)
                except Exception:
                    pass
            self._connections[bridge_id] = BridgeConnection(websocket, asyncio.Lock())

    async def disconnect(self, bridge_id: str) -> None:
        async with self._guard:
            self._connections.pop(bridge_id, None)

    def is_connected(self, bridge_id: str) -> bool:
        return bridge_id in self._connections

    async def handle_message(self, bridge_id: str, message: dict) -> None:
        message_type = message.get("type")
        if message_type == "result":
            command_id = str(message.get("command_id", ""))
            future = self._pending.pop(command_id, None)
            if future and not future.done():
                future.set_result(message)
        elif message_type == "pong":
            return

    async def send_command(
        self,
        bridge_id: str,
        command_type: str,
        payload: dict,
        timeout: float,
    ) -> dict:
        connection = self._connections.get(bridge_id)
        if not connection:
            raise RuntimeError("Bridge is not connected")
        command_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[command_id] = future
        command = {
            "type": "command",
            "command_id": command_id,
            "command_type": command_type,
            "payload": payload,
        }
        try:
            async with connection.lock:
                await connection.websocket.send_json(command)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise RuntimeError(f"Bridge command timed out after {timeout}s") from exc
        finally:
            self._pending.pop(command_id, None)


bridge_hub = BridgeHub()
