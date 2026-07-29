from __future__ import annotations

import asyncio
from typing import Any

MIN_AUTHORIZATION_TIMEOUT_SECONDS = 120.0
MAX_AUTHORIZATION_TIMEOUT_SECONDS = 300.0


def install_vtube_studio_auth_fix() -> None:
    from bridge import vtube_studio

    client_class = vtube_studio.VTubeStudioClient
    if getattr(client_class, "_aliver_authorization_timeout_fix", False):
        return

    original_public_config = vtube_studio.public_config
    original_request = client_class._request

    def patched_public_config(config: dict[str, Any]) -> dict[str, Any]:
        value = original_public_config(config)
        try:
            requested = float(
                value.get("authorization_timeout_seconds")
                or MIN_AUTHORIZATION_TIMEOUT_SECONDS
            )
        except (TypeError, ValueError):
            requested = MIN_AUTHORIZATION_TIMEOUT_SECONDS
        value["authorization_timeout_seconds"] = max(
            MIN_AUTHORIZATION_TIMEOUT_SECONDS,
            min(requested, MAX_AUTHORIZATION_TIMEOUT_SECONDS),
        )
        return value

    async def patched_request(
        self,
        message_type: str,
        data: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            return await original_request(self, message_type, data, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            if message_type == "AuthenticationTokenRequest":
                raise RuntimeError(
                    "已经连接到 VTube Studio 的 8001 端口，但等待插件授权超时。"
                    "请切回 VTube Studio 主画面，在 ALiver 插件授权弹窗中点击‘允许’，"
                    "然后重新启动会话。"
                ) from exc
            raise RuntimeError(f"VTube Studio API 请求超时：{message_type}") from exc

    vtube_studio.public_config = patched_public_config
    client_class._request = patched_request
    client_class._aliver_authorization_timeout_fix = True
