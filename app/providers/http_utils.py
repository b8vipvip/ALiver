from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

from app.providers.base import ProviderResult


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> ProviderResult:
    started = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )
        latency_ms = int((perf_counter() - started) * 1000)
        try:
            data = response.json()
        except ValueError:
            data = {"text": response.text[:4000]}
        if response.is_success:
            return ProviderResult(success=True, data=data, latency_ms=latency_ms)
        return ProviderResult(
            success=False,
            data=data,
            error=f"HTTP {response.status_code}",
            latency_ms=latency_ms,
        )
    except httpx.HTTPError as exc:
        return ProviderResult(
            success=False,
            error=str(exc),
            latency_ms=int((perf_counter() - started) * 1000),
        )
