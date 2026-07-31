from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.auto_director_service import wrap_director_instruction
from app.db import get_db
from app.director_service import dispatch_command
from app.json_utils import dumps
from app.live_run_service import live_run_recorder
from app.log_service import write_log
from app.models import BrowserExtension, DirectorCommand
from app.security import verify_token
from app.voice_service import (
    get_or_create_profile,
    handle_assistant_completed,
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
    auto_mute_chatgpt_tab: bool = False
    tts_api_base_url: str | None = None
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "shimmer"
    tts_speed: float = Field(default=1.03, ge=0.25, le=4.0)
    api_key: str | None = None
    clear_api_key: bool = False


class VoiceTestRequest(BaseModel):
    text: str = "你好呀，这是 ALiver 甜美语音的第一版测试。"


class AssistantCompletedRequest(BaseModel):
    message_id: str = ""
    text: str
    url: str = ""
    title: str = ""
    observed_at: str | None = None


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
    api_tts_notice = (
        "API TTS 已启用。第一版需要把 ChatGPT 切到文字对话，或手动静音该浏览器标签页，"
        "避免 ChatGPT 原声与 ALiver 合成音重叠。"
        if row.mode == "api_tts"
        else ""
    )
    return {
        "applied": True,
        "mode": row.mode,
        "commands": [{"id": style.id, "type": style.command_type, "status": style.status}],
        "native_voice": row.native_voice,
        "manual_native_voice_selection_required": row.mode == "chatgpt_live",
        "message": (
            f"语音表达风格已发送到当前 ChatGPT 对话。{api_tts_notice}"
            if api_tts_notice
            else "语音表达风格已发送到当前 ChatGPT 对话；内置音色请在 ChatGPT 语音设置中选择。"
        ),
    }


@router.post("/profiles/{extension_id}/unmute", dependencies=[Depends(require_admin_token)])
def unmute_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(BrowserExtension, extension_id) is None:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    return {
        "unmuted": False,
        "manual": True,
        "message": "第一版不自动修改浏览器标签页静音状态，请在 Chrome 标签页菜单中手动取消静音。",
    }


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


@router.post("/extensions/{extension_id}/assistant-completed")
async def assistant_completed(
    extension_id: str,
    payload: AssistantCompletedRequest,
    x_extension_token: str = Header(default="", alias="X-Extension-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    extension = db.get(BrowserExtension, extension_id)
    if extension is None or not verify_token(x_extension_token, extension.token_hash):
        raise HTTPException(status_code=401, detail="Invalid extension token")
    data = payload.model_dump()
    live_run_recorder.record_external(
        "chatgpt.assistant.completed",
        {"extension_id": extension_id, **data},
    )
    asyncio.create_task(handle_assistant_completed(extension_id, data))
    return {"accepted": True, "message_id": payload.message_id}


@router.get("/audio/{audio_id}")
def get_audio(audio_id: str, token: str) -> FileResponse:
    path = resolve_audio_file(audio_id, token)
    if path is None:
        raise HTTPException(status_code=404, detail="语音文件不存在或下载令牌已过期")
    return FileResponse(path, media_type="audio/wav", filename=path.name)
