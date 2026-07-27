from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    api_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, default="")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BridgeAgent(Base):
    __tablename__ = "bridge_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    machine_name: Mapped[str] = mapped_column(String(200), default="unknown")
    version: Mapped[str] = mapped_column(String(40), default="unknown")
    capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    token_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="offline", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BrowserExtension(Base):
    __tablename__ = "browser_extensions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    browser_name: Mapped[str] = mapped_column(String(80), default="Chrome")
    version: Mapped[str] = mapped_column(String(40), default="unknown")
    token_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="offline", index=True)
    active_tab_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AvatarSession(Base):
    __tablename__ = "avatar_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_config_id: Mapped[str] = mapped_column(String(36), ForeignKey("provider_configs.id"), index=True)
    bridge_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bridge_agents.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    external_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DirectorCommand(Base):
    __tablename__ = "director_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    extension_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("browser_extensions.id"), index=True
    )
    command_type: Mapped[str] = mapped_column(String(40), default="send_text", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AutoDirectorConfig(Base):
    __tablename__ = "auto_director_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    extension_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("browser_extensions.id"), unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="rules")
    api_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, default="")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_idle_prompt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AudienceEvent(Base):
    __tablename__ = "audience_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    config_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("auto_director_configs.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), default="comment", index=True)
    platform: Mapped[str] = mapped_column(String(40), default="manual", index=True)
    user_name: Mapped[str] = mapped_column(String(160), default="观众")
    content: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_command_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    bridge_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
