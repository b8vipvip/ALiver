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


class BrowserExtensionRegister(BaseModel):
    name: str = Field(default="ALiver ChatGPT Controller", min_length=1, max_length=120)
    browser_name: str = Field(default="Chrome", min_length=1, max_length=80)
    version: str = Field(default="0.1.0", min_length=1, max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserExtensionRegisterOut(BaseModel):
    extension_id: str
    token: str


class BrowserExtensionOut(BaseModel):
    id: str
    name: str
    browser_name: str
    version: str
    status: str
    connected: bool
    active_tab_url: str | None
    metadata: dict[str, Any]
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DirectorCommandCreate(BaseModel):
    extension_id: str
    command_type: str = Field(default="send_text", pattern="^(send_text|director_instruction)$")
    content: str = Field(min_length=1, max_length=12000)
    wrap_as_director: bool = True
    auto_send: bool = True
    force: bool = False
    priority: int = Field(default=50, ge=0, le=100)
    source: str = Field(default="manual", max_length=80)


class DirectorCommandOut(BaseModel):
    id: str
    extension_id: str
    command_type: str
    payload: dict[str, Any]
    result: dict[str, Any]
    status: str
    priority: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    dispatched_at: datetime | None
    completed_at: datetime | None


class AutoDirectorConfigUpsert(BaseModel):
    extension_id: str
    enabled: bool = False
    mode: str = Field(default="rules", pattern="^(rules|openai_compatible)$")
    api_base_url: str | None = Field(default=None, max_length=500)
    model_name: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)
    settings: dict[str, Any] = Field(default_factory=dict)


class AutoDirectorConfigOut(BaseModel):
    id: str | None
    extension_id: str
    enabled: bool
    mode: str
    api_base_url: str | None
    model_name: str | None
    credential_keys: list[str]
    settings: dict[str, Any]
    last_dispatched_at: datetime | None
    last_idle_prompt_at: datetime | None
    last_event_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class AudienceEventCreate(BaseModel):
    extension_id: str
    event_type: str = Field(default="comment", pattern="^(comment|gift|follow|like|share|system)$")
    platform: str = Field(default="manual", max_length=40)
    user_name: str = Field(default="观众", max_length=160)
    content: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class AudienceEventOut(BaseModel):
    id: str
    config_id: str
    event_type: str
    platform: str
    user_name: str
    content: str
    payload: dict[str, Any]
    status: str
    score: int
    reason: str | None
    selected_command_id: str | None
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AutoDirectorProcessOut(BaseModel):
    processed: bool
    action: str | None = None
    event_id: str | None = None
    command_id: str | None = None
    priority: int | None = None
    reason: str


class AutoDirectorStatusOut(BaseModel):
    extension_id: str
    configured: bool
    enabled: bool
    mode: str
    extension_connected: bool
    chatgpt_open: bool
    composer_ready: bool
    generating: bool
    queued_events: int
    selected_events: int
    ignored_events: int
    pending_commands: int
    last_dispatched_at: datetime | None
    last_event_at: datetime | None


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
