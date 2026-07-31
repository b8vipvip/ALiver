from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app import auto_director_service as service
from app.db import SessionLocal
from app.voice_service import decorate_director_content

_ORIGINAL_PROCESS_CONFIG = service.process_config
_ORIGINAL_WRAP = service.wrap_director_instruction
_CURRENT_EXTENSION_ID: ContextVar[str] = ContextVar("aliver_voice_extension_id", default="")


async def _process_config_with_voice_context(
    db: Any,
    config: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    token = _CURRENT_EXTENSION_ID.set(str(getattr(config, "extension_id", "") or ""))
    try:
        return await _ORIGINAL_PROCESS_CONFIG(db, config, force=force)
    finally:
        _CURRENT_EXTENSION_ID.reset(token)


def _wrap_with_voice_style(text: str) -> str:
    extension_id = _CURRENT_EXTENSION_ID.get()
    content = text
    if extension_id:
        try:
            with SessionLocal() as db:
                content = decorate_director_content(db, extension_id, text)
        except Exception:
            content = text
    return _ORIGINAL_WRAP(content)


def install_voice_director_patch() -> None:
    if getattr(service, "_aliver_voice_director_patch", False):
        return
    service.process_config = _process_config_with_voice_context
    service.wrap_director_instruction = _wrap_with_voice_style
    service._aliver_voice_director_patch = True
