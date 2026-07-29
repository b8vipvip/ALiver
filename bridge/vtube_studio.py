from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets

PROVIDER_TYPES = {"vtube_studio", "local_vtube_studio"}
API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"
DEFAULT_WS_URL = "ws://127.0.0.1:8001"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ws_url(value: Any) -> str:
    ws_url = str(value or DEFAULT_WS_URL).strip().rstrip("/")
    parsed = urlparse(ws_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("VTube Studio ws_url must use ws:// or wss://")
    if parsed.port is None:
        raise ValueError("VTube Studio ws_url must include a port, normally 8001")
    return ws_url


def token_file(config: dict[str, Any] | None = None) -> Path:
    configured = str((config or {}).get("token_file") or "").strip()
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured)))
    appdata = Path(os.environ.get("APPDATA") or Path.home())
    return appdata / "ALiver" / "secrets" / "vtube_studio.json"


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    hotkeys = config.get("hotkeys")
    return {
        "ws_url": normalize_ws_url(config.get("ws_url")),
        "plugin_name": str(config.get("plugin_name") or "ALiver"),
        "plugin_developer": str(config.get("plugin_developer") or "b8vipvip"),
        "require_model_loaded": bool(config.get("require_model_loaded", True)),
        "auto_reconnect": bool(config.get("auto_reconnect", True)),
        "reconnect_interval_seconds": float(config.get("reconnect_interval_seconds", 2.0)),
        "connect_timeout_seconds": float(config.get("connect_timeout_seconds", 12.0)),
        "authorization_timeout_seconds": float(config.get("authorization_timeout_seconds", 20.0)),
        "action_cooldown_ms": int(config.get("action_cooldown_ms", 1200)),
        "audio_device_name": str(
            config.get("audio_device_name") or "CABLE Output (VB-Audio Virtual Cable)"
        ),
        "mouth_input_parameter": str(config.get("mouth_input_parameter") or "VoiceVolume"),
        "mouth_output_parameter": str(config.get("mouth_output_parameter") or "ParamMouthOpenY"),
        "hotkeys": dict(hotkeys) if isinstance(hotkeys, dict) else {},
    }


class VTubeStudioClient:
    def __init__(self, config: dict[str, Any]):
        self.config = public_config(config)
        self.ws: Any = None
        self._request_lock = asyncio.Lock()
        self._connected_at: str | None = None

    @property
    def connected(self) -> bool:
        return self.ws is not None and not bool(getattr(self.ws, "closed", False))

    def _load_token(self) -> str | None:
        path = token_file(self.config)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        token = str(payload.get("authentication_token") or "").strip()
        return token or None

    def _save_token(self, token: str) -> None:
        path = token_file(self.config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "plugin_name": self.config["plugin_name"],
                    "plugin_developer": self.config["plugin_developer"],
                    "authentication_token": token,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def clear_token(self) -> None:
        try:
            token_file(self.config).unlink(missing_ok=True)
        except OSError:
            pass

    async def connect(self, *, force_authorize: bool = False) -> dict[str, Any]:
        await self.close()
        self.ws = await asyncio.wait_for(
            websockets.connect(
                self.config["ws_url"],
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=2 * 1024 * 1024,
            ),
            timeout=self.config["connect_timeout_seconds"],
        )
        self._connected_at = _utc_now()

        token = None if force_authorize else self._load_token()
        if token and await self._authenticate(token):
            return await self.snapshot()

        self.clear_token()
        token = await self._request_token()
        if not await self._authenticate(token):
            raise RuntimeError("VTube Studio rejected the ALiver plugin authentication")
        return await self.snapshot()

    async def ensure_connected(self) -> None:
        if self.connected:
            return
        await self.connect()

    async def close(self) -> None:
        ws = self.ws
        self.ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            pass

    async def _request(
        self,
        message_type: str,
        data: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("VTube Studio WebSocket is not connected")
        request_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "apiName": API_NAME,
            "apiVersion": API_VERSION,
            "requestID": request_id,
            "messageType": message_type,
        }
        if data is not None:
            payload["data"] = data

        async with self._request_lock:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(
                    self.ws.recv(),
                    timeout=timeout or self.config["connect_timeout_seconds"],
                )
                response = json.loads(raw)
                if response.get("requestID") != request_id:
                    continue
                if response.get("messageType") == "APIError":
                    error = response.get("data") or {}
                    raise RuntimeError(
                        "VTube Studio APIError "
                        f"{error.get('errorID')}: {error.get('message') or 'unknown error'}"
                    )
                return response.get("data") or {}

    async def _request_token(self) -> str:
        data = await self._request(
            "AuthenticationTokenRequest",
            {
                "pluginName": self.config["plugin_name"],
                "pluginDeveloper": self.config["plugin_developer"],
            },
            timeout=self.config["authorization_timeout_seconds"],
        )
        token = str(data.get("authenticationToken") or "").strip()
        if not token:
            raise RuntimeError("VTube Studio did not return an authentication token")
        self._save_token(token)
        return token

    async def _authenticate(self, token: str) -> bool:
        data = await self._request(
            "AuthenticationRequest",
            {
                "pluginName": self.config["plugin_name"],
                "pluginDeveloper": self.config["plugin_developer"],
                "authenticationToken": token,
            },
        )
        return bool(data.get("authenticated"))

    async def snapshot(self) -> dict[str, Any]:
        await self.ensure_connected()
        statistics = await self._request("StatisticsRequest")
        current_model = await self._request("CurrentModelRequest")
        hotkeys = await self._request("HotkeysInCurrentModelRequest", {})
        available = hotkeys.get("availableHotkeys")
        if not isinstance(available, list):
            available = []

        return {
            "connected": True,
            "authenticated": True,
            "connected_at": self._connected_at,
            "api": {
                "name": statistics.get("vTubeStudioVersion"),
                "version": statistics.get("vTubeStudioVersion"),
                "url": self.config["ws_url"],
                "framerate": statistics.get("framerate"),
                "connected_plugins": statistics.get("connectedPlugins"),
                "uptime_seconds": statistics.get("uptime"),
            },
            "model": {
                "loaded": bool(current_model.get("modelLoaded")),
                "name": current_model.get("modelName"),
                "id": current_model.get("modelID"),
                "json": current_model.get("vtsModelName"),
            },
            "hotkeys": available,
        }

    async def trigger_hotkey(self, identifier: str) -> dict[str, Any]:
        identifier = str(identifier or "").strip()
        if not identifier:
            raise ValueError("Hotkey name or ID is required")
        snapshot = await self.snapshot()
        hotkeys = snapshot.get("hotkeys") or []
        match = next(
            (
                item
                for item in hotkeys
                if str(item.get("hotkeyID") or "") == identifier
                or str(item.get("name") or "").casefold() == identifier.casefold()
            ),
            None,
        )
        if match is None:
            raise ValueError(f"VTube Studio hotkey not found: {identifier}")
        hotkey_id = str(match.get("hotkeyID") or "").strip()
        await self._request(
            "HotkeyTriggerRequest",
            {"hotkeyID": hotkey_id, "itemInstanceID": ""},
        )
        return {
            "triggered": True,
            "hotkey_id": hotkey_id,
            "hotkey_name": match.get("name"),
            "hotkey_type": match.get("type"),
        }


@dataclass
class VTubeStudioRuntime:
    session_id: str
    provider_id: str
    provider_name: str
    provider_type: str
    config: dict[str, Any]
    client: VTubeStudioClient
    state: dict[str, Any] = field(default_factory=dict)
    monitor_task: asyncio.Task | None = None
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    last_action_at: float = 0.0

    def status(self) -> dict[str, Any]:
        value = {
            "session_id": self.session_id,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "provider_type": self.provider_type,
            "status": self.state.get("status", "starting"),
            "config": public_config(self.config),
            "last_action": self.state.get("last_action"),
            "last_action_at": self.state.get("last_action_at"),
            "last_refresh_at": self.state.get("last_refresh_at"),
            "error": self.state.get("error"),
        }
        value.update(self.state.get("snapshot") or {})
        return value


class VTubeStudioSessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, VTubeStudioRuntime] = {}
        self._lock = asyncio.Lock()

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("provider.start_session requires session_id")

        plan = payload.get("provider_plan") or {}
        config = dict(plan.get("config") or {})
        provider_type = str(payload.get("provider_type") or "vtube_studio")
        if provider_type not in PROVIDER_TYPES:
            raise ValueError(f"Unsupported VTube Studio provider type: {provider_type}")

        async with self._lock:
            existing = self.sessions.get(session_id)
            if existing and existing.state.get("status") in {"active", "starting"}:
                return existing.status()

            client = VTubeStudioClient(config)
            runtime = VTubeStudioRuntime(
                session_id=session_id,
                provider_id=str(payload.get("provider_id") or ""),
                provider_name=str(payload.get("provider_name") or "VTube Studio"),
                provider_type=provider_type,
                config=public_config(config),
                client=client,
                state={"status": "starting", "started_at": _utc_now()},
            )
            self.sessions[session_id] = runtime

        try:
            snapshot = await client.connect()
            if runtime.config.get("require_model_loaded", True) and not snapshot["model"]["loaded"]:
                raise RuntimeError("VTube Studio is connected, but no Live2D model is loaded")
            runtime.state.update(
                {
                    "status": "active",
                    "snapshot": snapshot,
                    "last_refresh_at": _utc_now(),
                    "error": None,
                }
            )
            runtime.monitor_task = asyncio.create_task(
                self._monitor(runtime),
                name=f"vtube-studio-monitor-{session_id}",
            )
            return {
                **runtime.status(),
                "external_session_id": session_id,
                "message": "VTube Studio 已连接；当前模型、热键和配置已加载到数字人调试页面。",
            }
        except Exception as exc:
            runtime.state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            await client.close()
            raise

    async def _monitor(self, runtime: VTubeStudioRuntime) -> None:
        interval = max(1.0, float(runtime.config.get("reconnect_interval_seconds", 2.0)))
        while not runtime.stopped.is_set():
            try:
                await asyncio.wait_for(runtime.stopped.wait(), timeout=5.0)
                break
            except TimeoutError:
                pass
            try:
                snapshot = await runtime.client.snapshot()
                runtime.state.update(
                    {
                        "status": "active",
                        "snapshot": snapshot,
                        "last_refresh_at": _utc_now(),
                        "error": None,
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.state["error"] = f"{type(exc).__name__}: {exc}"
                runtime.state["status"] = "reconnecting"
                if not runtime.config.get("auto_reconnect", True):
                    runtime.state["status"] = "failed"
                    return
                try:
                    await asyncio.sleep(interval)
                    snapshot = await runtime.client.connect()
                    runtime.state.update(
                        {
                            "status": "active",
                            "snapshot": snapshot,
                            "last_refresh_at": _utc_now(),
                            "error": None,
                        }
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as reconnect_exc:
                    runtime.state["error"] = (
                        f"{type(reconnect_exc).__name__}: {reconnect_exc}"
                    )

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        if session_id:
            runtime = self.sessions.get(session_id)
            if runtime is None:
                return {
                    "session_id": session_id,
                    "status": "missing",
                    "error": "VTube Studio session is not running on this Bridge",
                }
            return runtime.status()
        return {key: runtime.status() for key, runtime in self.sessions.items()}

    def _runtime(self, session_id: str) -> VTubeStudioRuntime:
        runtime = self.sessions.get(session_id)
        if runtime is None:
            raise ValueError("VTube Studio session is not running on this Bridge")
        return runtime

    async def refresh(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        snapshot = await runtime.client.snapshot()
        runtime.state.update(
            {
                "status": "active",
                "snapshot": snapshot,
                "last_refresh_at": _utc_now(),
                "error": None,
            }
        )
        return runtime.status()

    async def authorize(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        runtime.client.clear_token()
        snapshot = await runtime.client.connect(force_authorize=True)
        runtime.state.update(
            {
                "status": "active",
                "snapshot": snapshot,
                "last_refresh_at": _utc_now(),
                "error": None,
            }
        )
        return runtime.status()

    async def action(
        self,
        session_id: str,
        *,
        action: str | None = None,
        hotkey: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        runtime = self._runtime(session_id)
        action_name = str(action or "").strip().lower()
        identifier = str(hotkey or "").strip()
        if not identifier and action_name:
            identifier = str((runtime.config.get("hotkeys") or {}).get(action_name) or "").strip()
        if not identifier:
            raise ValueError(
                f"No VTube Studio hotkey is configured for action: {action_name or 'unknown'}"
            )

        now = time.monotonic()
        cooldown = max(0, int(runtime.config.get("action_cooldown_ms", 1200))) / 1000
        if not force and now - runtime.last_action_at < cooldown:
            return {
                **runtime.status(),
                "action_skipped": True,
                "reason": "cooldown",
                "requested_action": action_name or None,
                "requested_hotkey": identifier,
            }

        result = await runtime.client.trigger_hotkey(identifier)
        runtime.last_action_at = now
        runtime.state["last_action"] = action_name or result.get("hotkey_name")
        runtime.state["last_action_at"] = _utc_now()
        runtime.state["snapshot"] = await runtime.client.snapshot()
        return {**runtime.status(), "action_result": result}

    async def stop(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            runtime = self.sessions.pop(session_id, None)
        if runtime is None:
            return {
                "session_id": session_id,
                "status": "ended",
                "already_stopped": True,
            }
        runtime.stopped.set()
        if runtime.monitor_task is not None:
            runtime.monitor_task.cancel()
            await asyncio.gather(runtime.monitor_task, return_exceptions=True)
        await runtime.client.close()
        runtime.state["status"] = "ended"
        runtime.state["ended_at"] = _utc_now()
        return runtime.status()

    async def stop_all(self) -> None:
        for session_id in list(self.sessions):
            await self.stop(session_id)
