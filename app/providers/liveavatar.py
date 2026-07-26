from __future__ import annotations

from typing import Any

from app.providers.base import AvatarProvider, ProviderResult


class LiveAvatarProvider(AvatarProvider):
    """Bridge-managed adapter.

    LiveAvatar LITE uses a WebRTC client/agent pipeline. The server keeps provider
    configuration and session audit data, while the Windows Bridge owns the media
    connection and SDK lifecycle.
    """

    provider_type = "liveavatar"
    execution_mode = "bridge"

    async def test_connection(self) -> ProviderResult:
        missing = []
        if not self.context.settings.get("avatar_id"):
            missing.append("settings.avatar_id")
        if not self.context.settings.get("transport"):
            missing.append("settings.transport")
        if missing:
            return ProviderResult(success=False, error=f"Missing: {', '.join(missing)}")
        return ProviderResult(
            success=True,
            data={
                "message": "LiveAvatar configuration is ready for Bridge execution",
                "execution_mode": self.execution_mode,
                "transport": self.context.settings.get("transport"),
            },
        )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        payload = dict(self.context.settings)
        payload.update(overrides)
        return ProviderResult(
            success=True,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.start_session",
                "provider_type": self.provider_type,
                "config": payload,
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
