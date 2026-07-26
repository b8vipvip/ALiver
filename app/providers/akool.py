from __future__ import annotations

from typing import Any

from app.providers.base import AvatarProvider, ProviderResult
from app.providers.http_utils import request_json


class AkoolProvider(AvatarProvider):
    provider_type = "akool"

    @property
    def base_url(self) -> str:
        return (self.context.api_base_url or "https://openapi.akool.com").rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        api_key = str(self.context.credentials.get("api_key", ""))
        return {"x-api-key": api_key, "Content-Type": "application/json"}

    async def test_connection(self) -> ProviderResult:
        if not self.context.credentials.get("api_key"):
            return ProviderResult(success=False, error="Missing credentials.api_key")
        return await request_json(
            "GET",
            f"{self.base_url}/api/open/v4/liveAvatar/session/list",
            headers=self.headers,
            params={"page": 1, "size": 1},
        )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        if not self.context.credentials.get("api_key"):
            return ProviderResult(success=False, error="Missing credentials.api_key")
        payload = dict(self.context.settings)
        payload.update(overrides)
        result = await request_json(
            "POST",
            f"{self.base_url}/api/open/v4/liveAvatar/session/create",
            headers=self.headers,
            json_body=payload,
        )
        if result.success:
            data = result.data.get("data") if isinstance(result.data, dict) else None
            if isinstance(data, dict):
                result.external_session_id = str(data.get("_id") or data.get("id") or "") or None
                result.data = data
        return result

    async def stop_session(
        self, external_session_id: str | None, session_data: dict[str, Any]
    ) -> ProviderResult:
        if not external_session_id:
            return ProviderResult(success=False, error="Missing external AKOOL session id")
        return await request_json(
            "POST",
            f"{self.base_url}/api/open/v4/liveAvatar/session/close",
            headers=self.headers,
            json_body={"id": external_session_id},
        )
