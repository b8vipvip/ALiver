from __future__ import annotations

from typing import Any

from app.providers.base import AvatarProvider, ProviderResult
from app.providers.http_utils import request_json


class TavusProvider(AvatarProvider):
    provider_type = "tavus"

    @property
    def base_url(self) -> str:
        return (self.context.api_base_url or "https://tavusapi.com").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        api_key = str(self.context.credentials.get("api_key", ""))
        return {"x-api-key": api_key, "Content-Type": "application/json"}

    async def test_connection(self) -> ProviderResult:
        if not self.context.credentials.get("api_key"):
            return ProviderResult(success=False, error="Missing credentials.api_key")
        return await request_json(
            "GET",
            f"{self.base_url}/v2/conversations",
            headers=self.headers,
            params={"limit": 1},
        )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        if not self.context.credentials.get("api_key"):
            return ProviderResult(success=False, error="Missing credentials.api_key")
        payload = {
            key: value
            for key, value in self.context.settings.items()
            if key
            in {
                "replica_id",
                "persona_id",
                "audio_only",
                "callback_url",
                "conversation_name",
                "conversational_context",
                "custom_greeting",
                "memory_stores",
                "test_mode",
                "require_auth",
                "max_participants",
                "properties",
            }
        }
        payload.update(overrides)
        result = await request_json(
            "POST",
            f"{self.base_url}/v2/conversations",
            headers=self.headers,
            json_body=payload,
        )
        if result.success:
            result.external_session_id = str(result.data.get("conversation_id") or "") or None
        return result

    async def stop_session(
        self, external_session_id: str | None, session_data: dict[str, Any]
    ) -> ProviderResult:
        if not external_session_id:
            return ProviderResult(success=False, error="Missing external conversation_id")
        return await request_json(
            "POST",
            f"{self.base_url}/v2/conversations/{external_session_id}/end",
            headers=self.headers,
        )
