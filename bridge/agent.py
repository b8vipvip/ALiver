from __future__ import annotations

import asyncio
import json
import os
import platform
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

try:
    from bridge.audio_capture import AudioCaptureManager
    from bridge.gpt_in_speech import DEFAULT_TEST_TEXT, play_gpt_in_test_speech
except ModuleNotFoundError:
    from audio_capture import AudioCaptureManager
    from gpt_in_speech import DEFAULT_TEST_TEXT, play_gpt_in_test_speech

BRIDGE_VERSION = "0.3.1"
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state.json"
LOCAL_CONFIG = BASE_DIR / "bridge.local.json"
EXAMPLE_CONFIG = BASE_DIR / "bridge.example.json"


@dataclass
class ManagedProcess:
    process_id: str
    popen: subprocess.Popen


class BridgeAgent:
    def __init__(self) -> None:
        self.config = self.load_config()
        self.state = self.load_state()
        self.processes: dict[str, ManagedProcess] = {}
        self.audio = AudioCaptureManager()
        self.stop_event = asyncio.Event()

    def load_config(self) -> dict[str, Any]:
        if not LOCAL_CONFIG.exists():
            LOCAL_CONFIG.write_text(
                EXAMPLE_CONFIG.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            print(f"Created {LOCAL_CONFIG}. Edit process paths if needed.")
        return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))

    def load_state(self) -> dict[str, Any]:
        if not STATE_FILE.exists():
            return {}
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))

    def save_state(self) -> None:
        STATE_FILE.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def server_url(self) -> str:
        return str(self.config.get("server_url", "http://127.0.0.1:8765")).rstrip("/")

    @staticmethod
    def capabilities() -> list[str]:
        return [
            "system.info",
            "process.list",
            "process.start",
            "process.stop",
            "audio.routes.scan",
            "audio.routes.get",
            "audio.routes.save",
            "audio.routes.auto",
            "audio.gpt_out.start",
            "audio.gpt_out.status",
            "audio.gpt_out.stop",
            "audio.gpt_in.test",
            "audio.devices",
            "audio.capture.start",
            "audio.capture.status",
            "audio.capture.stop",
            "provider.liveavatar.placeholder",
        ]

    async def register(self) -> None:
        payload = {
            "name": self.config.get("name", "Windows AI Live Bridge"),
            "machine_name": socket.gethostname(),
            "version": BRIDGE_VERSION,
            "capabilities": self.capabilities(),
            "metadata": self.system_info(),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.server_url}/api/bridges/register", json=payload)
            response.raise_for_status()
            self.state = response.json()
            self.save_state()
        print(f"Registered Bridge: {self.state['bridge_id']}")

    async def sync_registration(self) -> None:
        bridge_id = self.state.get("bridge_id")
        token = self.state.get("token")
        if not bridge_id or not token:
            await self.register()
            return
        payload = {
            "version": BRIDGE_VERSION,
            "capabilities": self.capabilities(),
            "metadata": self.system_info(),
        }
        headers = {"X-Bridge-Token": str(token)}
        async with httpx.AsyncClient(timeout=20) as client:
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

    def ws_url(self) -> str:
        parsed = urlparse(self.server_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return (
            f"{scheme}://{parsed.netloc}/ws/bridges/"
            f"{self.state['bridge_id']}?token={self.state['token']}"
        )

    def system_info(self) -> dict[str, Any]:
        return {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "bridge_version": BRIDGE_VERSION,
            "audio_capture": self.audio.status(),
        }

    async def run(self) -> None:
        await self.sync_registration()
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url(),
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    print("Bridge connected to ALiver")
                    heartbeat = asyncio.create_task(self.heartbeat_loop(ws))
                    try:
                        async for raw in ws:
                            message = json.loads(raw)
                            if message.get("type") == "command":
                                asyncio.create_task(self.handle_command(ws, message))
                    finally:
                        heartbeat.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Bridge disconnected: {exc}. Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

    async def heartbeat_loop(self, ws) -> None:
        interval = float(self.config.get("heartbeat_seconds", 10))
        while True:
            await ws.send(json.dumps({"type": "heartbeat", "metadata": self.system_info()}))
            await asyncio.sleep(interval)

    async def handle_command(self, ws, message: dict[str, Any]) -> None:
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
        except Exception as exc:
            response = {
                "type": "result",
                "command_id": command_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        await ws.send(json.dumps(response, ensure_ascii=False))

    async def execute(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "ping":
            return {"pong": True}
        if command_type == "system.info":
            return self.system_info()
        if command_type == "process.list":
            return {
                "configured": sorted((self.config.get("processes") or {}).keys()),
                "running": {
                    key: {"pid": item.popen.pid, "alive": item.popen.poll() is None}
                    for key, item in self.processes.items()
                },
            }
        if command_type == "process.start":
            return self.start_process(str(payload.get("process_id", "")))
        if command_type == "process.stop":
            return self.stop_process(str(payload.get("process_id", "")))

        if command_type in {"audio.routes.scan", "audio.devices"}:
            return await asyncio.to_thread(self.audio.list_devices)
        if command_type == "audio.routes.get":
            return await asyncio.to_thread(self.audio.get_routes)
        if command_type == "audio.routes.auto":
            return await asyncio.to_thread(self.audio.apply_recommendations)
        if command_type == "audio.routes.save":
            return await asyncio.to_thread(
                self.audio.save_routes,
                gpt_out_capture_key=str(payload.get("gpt_out_capture_key", "")),
                gpt_in_playback_key=str(payload.get("gpt_in_playback_key", "")),
            )
        if command_type in {"audio.gpt_out.start", "audio.capture.start"}:
            if command_type == "audio.capture.start" and payload.get("device_index") is not None:
                return await asyncio.to_thread(
                    self.audio.start,
                    int(payload["device_index"]),
                    chunk_size=int(payload.get("chunk_size", 1024)),
                    save_wav=bool(payload.get("save_wav", True)),
                    wav_seconds=float(payload.get("wav_seconds", 10)),
                    auto_stop=bool(payload.get("auto_stop", False)),
                )
            return await asyncio.to_thread(
                self.audio.start_gpt_out,
                duration_seconds=float(payload.get("duration_seconds", 10)),
                save_wav=bool(payload.get("save_wav", True)),
                auto_stop=bool(payload.get("auto_stop", True)),
                chunk_size=int(payload.get("chunk_size", 1024)),
            )
        if command_type in {"audio.gpt_out.status", "audio.capture.status"}:
            return self.audio.status()
        if command_type in {"audio.gpt_out.stop", "audio.capture.stop"}:
            return await asyncio.to_thread(self.audio.stop)
        if command_type == "audio.gpt_in.test":
            return await asyncio.to_thread(
                play_gpt_in_test_speech,
                self.audio,
                text=str(payload.get("text") or DEFAULT_TEST_TEXT),
            )

        if command_type == "provider.start_session":
            return await self.start_provider_session(payload)
        if command_type == "provider.stop_session":
            return await self.stop_provider_session(payload)
        raise ValueError(f"Unsupported command: {command_type}")

    def start_process(self, process_id: str) -> dict[str, Any]:
        definitions = self.config.get("processes") or {}
        definition = definitions.get(process_id)
        if not definition:
            raise ValueError("Unknown process_id. Add it to bridge.local.json first.")
        current = self.processes.get(process_id)
        if current and current.popen.poll() is None:
            return {
                "process_id": process_id,
                "pid": current.popen.pid,
                "already_running": True,
            }
        command = definition.get("command")
        if not isinstance(command, list) or not command:
            raise ValueError("Configured command must be a non-empty list")
        popen = subprocess.Popen(command, cwd=definition.get("cwd") or None)
        self.processes[process_id] = ManagedProcess(process_id=process_id, popen=popen)
        return {"process_id": process_id, "pid": popen.pid, "started": True}

    def stop_process(self, process_id: str) -> dict[str, Any]:
        item = self.processes.get(process_id)
        if not item or item.popen.poll() is not None:
            return {"process_id": process_id, "stopped": True, "already_stopped": True}
        item.popen.terminate()
        try:
            item.popen.wait(timeout=8)
        except subprocess.TimeoutExpired:
            item.popen.kill()
        return {"process_id": process_id, "stopped": True}

    async def start_provider_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_type = payload.get("provider_type")
        if provider_type != "liveavatar":
            raise ValueError(f"Bridge-managed provider not implemented: {provider_type}")
        plan = payload.get("provider_plan") or {}
        config = plan.get("config") or {}
        return {
            "status": "awaiting_manual",
            "external_session_id": None,
            "message": (
                "LiveAvatar Bridge connector scaffold is active. "
                "The GPT_OUT route will become its PCM source in the provider phase."
            ),
            "config_received": {
                "avatar_id": config.get("avatar_id"),
                "transport": config.get("transport"),
                "mode": config.get("mode", "LITE"),
            },
        }

    async def stop_provider_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ended", "message": "Bridge session resources released"}


async def main() -> None:
    agent = BridgeAgent()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, agent.stop_event.set)
        except NotImplementedError:
            pass
    try:
        await agent.run()
    finally:
        await asyncio.to_thread(agent.audio.shutdown)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
