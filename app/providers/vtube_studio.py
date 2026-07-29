from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from app.providers.base import AvatarProvider, ProviderResult

DEFAULT_WS_URL = "ws://127.0.0.1:8001"
DEFAULT_HOTKEYS = {
    "idle": "",
    "talking": "",
    "thinking": "",
    "wave": "",
    "happy": "",
    "surprised": "",
    "reset": "",
}


def _normalize_ws_url(value: Any) -> str:
    ws_url = str(value or DEFAULT_WS_URL).strip()
    parsed = urlparse(ws_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("settings.ws_url must be a valid ws:// or wss:// URL")
    if parsed.port is None:
        raise ValueError("settings.ws_url must include the VTube Studio API port, normally 8001")
    return ws_url.rstrip("/")


def _normalize_hotkeys(value: Any) -> dict[str, str]:
    result = dict(DEFAULT_HOTKEYS)
    if value is None:
        return result
    if not isinstance(value, dict):
        raise ValueError("settings.hotkeys must be a JSON object")
    for key, hotkey in value.items():
        name = str(key).strip().lower()
        if name not in result:
            continue
        result[name] = str(hotkey or "").strip()[:200]
    return result


class VTubeStudioProvider(AvatarProvider):
    """Local VTube Studio provider executed by the Windows Bridge."""

    provider_type = "vtube_studio"
    execution_mode = "bridge"

    def _runtime_config(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        values = dict(self.context.settings)
        values.update(overrides or {})

        plugin_name = str(values.get("plugin_name") or "ALiver").strip()
        plugin_developer = str(values.get("plugin_developer") or "b8vipvip").strip()
        if not plugin_name:
            raise ValueError("settings.plugin_name is required")
        if not plugin_developer:
            raise ValueError("settings.plugin_developer is required")

        return {
            "ws_url": _normalize_ws_url(values.get("ws_url")),
            "plugin_name": plugin_name[:32],
            "plugin_developer": plugin_developer[:32],
            "require_model_loaded": bool(values.get("require_model_loaded", True)),
            "auto_reconnect": bool(values.get("auto_reconnect", True)),
            "reconnect_interval_seconds": max(
                0.5, min(float(values.get("reconnect_interval_seconds", 2.0)), 30.0)
            ),
            "connect_timeout_seconds": max(
                3.0, min(float(values.get("connect_timeout_seconds", 12.0)), 60.0)
            ),
            "authorization_timeout_seconds": max(
                30.0, min(float(values.get("authorization_timeout_seconds", 120.0)), 300.0)
            ),
            "action_cooldown_ms": max(
                0, min(int(values.get("action_cooldown_ms", 1200)), 30000)
            ),
            "audio_device_name": str(
                values.get("audio_device_name") or "CABLE Output (VB-Audio Virtual Cable)"
            )[:240],
            "mouth_input_parameter": str(values.get("mouth_input_parameter") or "VoiceVolume")[:120],
            "mouth_output_parameter": str(values.get("mouth_output_parameter") or "ParamMouthOpenY")[:120],
            "hotkeys": _normalize_hotkeys(values.get("hotkeys")),
        }

    async def test_connection(self) -> ProviderResult:
        started = time.perf_counter()
        try:
            config = self._runtime_config()
        except (TypeError, ValueError) as exc:
            return ProviderResult(success=False, error=str(exc))

        return ProviderResult(
            success=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
            data={
                "message": "VTube Studio 配置有效；请在会话或数字人调试页通过 Bridge 完成真实连接与首次授权。",
                "execution_mode": self.execution_mode,
                "provider_type": self.provider_type,
                "live_test_required": True,
                "ws_url": config["ws_url"],
                "plugin_name": config["plugin_name"],
                "require_model_loaded": config["require_model_loaded"],
                "authorization_timeout_seconds": config["authorization_timeout_seconds"],
            },
        )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        try:
            config = self._runtime_config(overrides)
        except (TypeError, ValueError) as exc:
            return ProviderResult(success=False, error=str(exc))
        return ProviderResult(
            success=True,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.start_session",
                "provider_type": self.provider_type,
                "config": config,
            },
        )

    async def stop_session(
        self, external_session_id: str | None, session_data: dict[str, Any]
    ) -> ProviderResult:
        return ProviderResult(
            success=True,
            external_session_id=external_session_id,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.stop_session",
                "provider_type": self.provider_type,
            },
        )
