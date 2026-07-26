from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(slots=True)
class ProviderContext:
    provider_id: str
    name: str
    provider_type: str
    api_base_url: str | None
    credentials: dict[str, Any]
    settings: dict[str, Any]


@dataclass(slots=True)
class ProviderResult:
    success: bool
    external_session_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None


class AvatarProvider(ABC):
    provider_type: ClassVar[str]
    execution_mode: ClassVar[str] = "server_http"

    def __init__(self, context: ProviderContext):
        self.context = context

    @abstractmethod
    async def test_connection(self) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def stop_session(
        self, external_session_id: str | None, session_data: dict[str, Any]
    ) -> ProviderResult:
        raise NotImplementedError
