from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.auto_director_service import wrap_director_instruction
from app.db import get_db
from app.director_service import dispatch_command
from app.json_utils import dumps
from app.log_service import write_log
from app.models import BrowserExtension, DirectorCommand
from app.voice_service import (
    get_or_create_profile,
    profile_catalog,
    profile_to_dict,
    resolve_audio_file,
    style_apply_prompt,
    test_voice_profile,
    update_profile,
)

router = APIRouter(prefix="/api/voice", tags=["voice"])


class VoiceProfileUpdate(BaseModel):
    enabled: bool = False
    mode: str = "chatgpt_live"
    bridge_id: str | None = None
    style_preset: str = "sweet_young"
    native_voice: str = "Maple"
    style_instruction: str = ""
    auto_apply_style: bool = True
    auto_mute_chatgpt_tab: bool = True
    tts_api_base_url: str | None = None
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "shimmer"
    tts_speed: float = Field(default=1.03, ge=0.25, le=4.0)
    api_key: str | None = None
    clear_api_key: bool = False


class VoiceTestRequest(BaseModel):
    text: str = "你好呀，这是 ALiver 甜美语音的第一版测试。"


async def _queue_extension_command(
    db: Session,
    extension_id: str,
    command_type: str,
    payload: dict[str, Any],
    *,
    priority: int = 90,
) -> DirectorCommand:
    extension = db.get(BrowserExtension, extension_id)
    if extension is None:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    row = DirectorCommand(
        extension_id=extension_id,
        command_type=command_type,
        payload_json=dumps(payload),
        status="queued",
        priority=priority,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        await dispatch_command(db, row)
    except RuntimeError as exc:
        row.status = "queued"
        row.error_message = str(exc)
        db.commit()
    return row


@router.get("/catalog", dependencies=[Depends(require_admin_token)])
def catalog() -> dict[str, Any]:
    return profile_catalog()


@router.get("/profiles/{extension_id}", dependencies=[Depends(require_admin_token)])
def get_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(BrowserExtension, extension_id) is None:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    return profile_to_dict(get_or_create_profile(db, extension_id))


@router.put("/profiles/{extension_id}", dependencies=[Depends(require_admin_token)])
def save_profile(
    extension_id: str,
    payload: VoiceProfileUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if db.get(BrowserExtension, extension_id) is None:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    row = get_or_create_profile(db, extension_id)
    try:
        row = update_profile(db, row, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_log(
        db,
        category="voice.profile.updated",
        message="语音音色配置已保存",
        details={
            "extension_id": extension_id,
            "mode": row.mode,
            "preset": row.style_preset,
            "native_voice": row.native_voice,
            "tts_voice": row.tts_voice,
        },
    )
    return profile_to_dict(row)


@router.post("/profiles/{extension_id}/apply", dependencies=[Depends(require_admin_token)])
async def apply_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = get_or_create_profile(db, extension_id)
    commands: list[dict[str, Any]] = []
    if row.mode == "api_tts" and row.auto_mute_chatgpt_tab:
        mute = await _queue_extension_command(
            db,
            extension_id,
            "voice_tab_mute",
            {"muted": True, "source": "voice_profile_apply"},
            priority=99,
        )
        commands.append({"id": mute.id, "type": mute.command_type, "status": mute.status})
    style = await _queue_extension_command(
        db,
        extension_id,
        "director_instruction",
        {
            "text": wrap_director_instruction(style_apply_prompt(row)),
            "auto_send": True,
            "force": False,
            "source": "voice_profile_apply",
        },
        priority=98,
    )
    commands.append({"id": style.id, "type": style.command_type, "status": style.status})
    return {
        "applied": True,
        "mode": row.mode,
        "commands": commands,
        "message": (
            "已静音绑定的 ChatGPT 标签页，并启用 ALiver API TTS。"
            if row.mode == "api_tts" and row.auto_mute_chatgpt_tab
            else "语音表达风格已发送到当前 ChatGPT 对话。"
        ),
    }


@router.post("/profiles/{extension_id}/unmute", dependencies=[Depends(require_admin_token)])
async def unmute_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    command = await _queue_extension_command(
        db,
        extension_id,
        "voice_tab_mute",
        {"muted": False, "source": "voice_profile_unmute"},
        priority=99,
    )
    return {"unmuted": True, "command_id": command.id, "status": command.status}


@router.post("/profiles/{extension_id}/test", dependencies=[Depends(require_admin_token)])
async def test_profile(
    extension_id: str,
    payload: VoiceTestRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = get_or_create_profile(db, extension_id)
    try:
        return await test_voice_profile(db, row, payload.text)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS 接口调用失败：{type(exc).__name__}: {exc}") from exc


@router.get("/audio/{audio_id}")
def get_audio(audio_id: str, token: str) -> FileResponse:
    path = resolve_audio_file(audio_id, token)
    if path is None:
        raise HTTPException(status_code=404, detail="语音文件不存在或下载令牌已过期")
    return FileResponse(path, media_type="audio/wav", filename=path.name)
