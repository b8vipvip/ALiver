from __future__ import annotations

import asyncio
from uuid import uuid4

from app.providers.base import AvatarProvider, ProviderResult


class MockProvider(AvatarProvider):
    provider_type = "mock"

    async def test_connection(self) -> ProviderResult:
        await asyncio.sleep(0.05)
        return ProviderResult(success=True, data={"message": "Mock provider ready"}, latency_ms=50)

    async def create_session(self, overrides: dict) -> ProviderResult:
        await asyncio.sleep(0.05)
        session_id = f"mock-{uuid4()}"
        return ProviderResult(
            success=True,
            external_session_id=session_id,
            data={
                "status": "active",
                "preview_url": "about:blank",
                "echo": overrides,
            },
            latency_ms=50,
        )

    async def stop_session(self, external_session_id: str | None, session_data: dict) -> ProviderResult:
        await asyncio.sleep(0.02)
        return ProviderResult(
            success=True,
            external_session_id=external_session_id,
            data={"status": "ended"},
            latency_ms=20,
        )
