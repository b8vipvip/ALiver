from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(pattern="^(mock|tavus|akool|liveavatar)$")
    enabled: bool = True
    api_base_url: str | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    api_base_url: str | None = None
    credentials: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider_type: str
    enabled: bool
    api_base_url: str | None
    credential_keys: list[str]
    settings: dict[str, Any]
    execution_mode: str
    created_at: datetime
    updated_at: datetime


class SessionCreate(BaseModel):
    provider_config_id: str
    bridge_id: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class SessionOut(BaseModel):
    id: str
    provider_config_id: str
    provider_name: str | None = None
    provider_type: str | None = None
    bridge_id: str | None
    status: str
    external_session_id: str | None
    request: dict[str, Any]
    response: dict[str, Any]
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BridgeRegister(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    machine_name: str = "unknown"
    version: str = "unknown"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BridgeRegisterOut(BaseModel):
    bridge_id: str
    token: str


class BridgeHeartbeat(BaseModel):
    version: str | None = None
    capabilities: list[str] | None = None
    metadata: dict[str, Any] | None = None


class BridgeOut(BaseModel):
    id: str
    name: str
    machine_name: str
    version: str
    capabilities: list[str]
    metadata: dict[str, Any]
    status: str
    connected: bool
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BridgeCommandRequest(BaseModel):
    command_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class LogOut(BaseModel):
    id: int
    level: str
    category: str
    message: str
    details: dict[str, Any]
    provider_id: str | None
    session_id: str | None
    bridge_id: str | None
    latency_ms: int | None
    created_at: datetime
