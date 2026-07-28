from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from fastapi import WebSocket


@dataclass(slots=True)
class BridgeConnection:
    connection_id: str
    websocket: WebSocket
    lock: asyncio.Lock


@dataclass(slots=True)
class PendingCommand:
    bridge_id: str
    connection_id: str
    future: asyncio.Future


class BridgeHub:
    def __init__(self) -> None:
        self._connections: dict[str, BridgeConnection] = {}
        self._pending: dict[str, PendingCommand] = {}
        self._guard = asyncio.Lock()

    async def connect(self, bridge_id: str, websocket: WebSocket) -> str:
        await websocket.accept()
        connection = BridgeConnection(str(uuid4()), websocket, asyncio.Lock())
        old: BridgeConnection | None = None
        async with self._guard:
            old = self._connections.get(bridge_id)
            self._connections[bridge_id] = connection
        if old is not None and old.websocket is not websocket:
            try:
                await old.websocket.close(code=1012, reason="Bridge connection replaced")
            except Exception:
                pass
        return connection.connection_id

    async def disconnect(self, bridge_id: str, connection_id: str | None = None) -> bool:
        pending_to_fail: list[asyncio.Future] = []
        async with self._guard:
            current = self._connections.get(bridge_id)
            if current is None:
                return False
            if connection_id is not None and current.connection_id != connection_id:
                return False
            self._connections.pop(bridge_id, None)
            for command_id, pending in list(self._pending.items()):
                if pending.bridge_id == bridge_id and pending.connection_id == current.connection_id:
                    self._pending.pop(command_id, None)
                    pending_to_fail.append(pending.future)
        for future in pending_to_fail:
            if not future.done():
                future.set_exception(RuntimeError("Bridge connection was interrupted"))
        return True

    def is_connected(self, bridge_id: str) -> bool:
        return bridge_id in self._connections

    def connection_id(self, bridge_id: str) -> str | None:
        connection = self._connections.get(bridge_id)
        return connection.connection_id if connection else None

    async def handle_message(
        self,
        bridge_id: str,
        message: dict,
        *,
        connection_id: str | None = None,
    ) -> None:
        if connection_id is not None:
            current = self._connections.get(bridge_id)
            if current is None or current.connection_id != connection_id:
                return
        message_type = message.get("type")
        if message_type == "result":
            command_id = str(message.get("command_id", ""))
            pending = self._pending.pop(command_id, None)
            if pending and not pending.future.done():
                pending.future.set_result(message)
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
        self._pending[command_id] = PendingCommand(
            bridge_id=bridge_id,
            connection_id=connection.connection_id,
            future=future,
        )
        command = {
            "type": "command",
            "command_id": command_id,
            "command_type": command_type,
            "payload": payload,
        }
        try:
            async with connection.lock:
                current = self._connections.get(bridge_id)
                if current is None or current.connection_id != connection.connection_id:
                    raise RuntimeError("Bridge connection changed before the command was sent")
                await connection.websocket.send_json(command)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise RuntimeError(f"Bridge command timed out after {timeout}s") from exc
        finally:
            self._pending.pop(command_id, None)


bridge_hub = BridgeHub()
