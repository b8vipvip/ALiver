from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VoiceProfile(Base):
    """Voice delivery settings bound to one Chrome director extension."""

    __tablename__ = "voice_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    extension_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("browser_extensions.id"), unique=True, index=True
    )
    bridge_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bridge_agents.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), default="chatgpt_live")
    style_preset: Mapped[str] = mapped_column(String(64), default="sweet_young")
    native_voice: Mapped[str] = mapped_column(String(64), default="Maple")
    style_instruction: Mapped[str] = mapped_column(Text, default="")
    native_tuning_json: Mapped[str] = mapped_column(Text, default="{}")
    auto_apply_style: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_mute_chatgpt_tab: Mapped[bool] = mapped_column(Boolean, default=False)
    tts_api_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tts_model: Mapped[str] = mapped_column(String(200), default="gpt-4o-mini-tts")
    tts_voice: Mapped[str] = mapped_column(String(120), default="shimmer")
    tts_speed_percent: Mapped[int] = mapped_column(Integer, default=103)
    credentials_encrypted: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
