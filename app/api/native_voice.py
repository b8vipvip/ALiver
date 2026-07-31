from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.voice import _queue_extension_command
from app.auth import require_admin_token
from app.auto_director_service import wrap_director_instruction
from app.db import get_db
from app.log_service import write_log
from app.models import BrowserExtension
from app.native_voice_tuning import (
    DEFAULT_NATIVE_TUNING,
    TUNING_LIMITS,
    native_profile_dict,
    render_native_instruction,
    save_native_tuning,
)
from app.voice_service import (
    CHATGPT_NATIVE_VOICES,
    STYLE_PRESETS,
    get_or_create_profile,
    style_apply_prompt,
    update_profile,
)

router = APIRouter(
    prefix="/api/native-voice",
    tags=["native-voice"],
    dependencies=[Depends(require_admin_token)],
)


class NativeVoiceProfileUpdate(BaseModel):
    enabled: bool = False
    style_preset: str = "sweet_young"
    native_voice: str = "Maple"
    style_instruction: str = ""
    auto_apply_style: bool = True
    native_tuning: dict[str, float] = Field(default_factory=dict)


class NativeVoiceTestRequest(BaseModel):
    text: str = "你好呀，欢迎来到直播间。今天我们轻松聊聊 AI 和生活趣事。"


def _ensure_extension(db: Session, extension_id: str) -> None:
    if db.get(BrowserExtension, extension_id) is None:
        raise HTTPException(status_code=404, detail="未找到 Chrome 导演扩展")


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    return {
        "style_presets": STYLE_PRESETS,
        "chatgpt_native_voices": CHATGPT_NATIVE_VOICES,
        "default_tuning": DEFAULT_NATIVE_TUNING,
        "tuning_limits": TUNING_LIMITS,
        "notes": {
            "latency": "本模式使用 ChatGPT Live 原生语音，不增加二次 TTS 合成延迟。",
            "precision": "ChatGPT Live 支持语气和语速要求，但不提供精确的播放器音高或速度控制。",
        },
    }


@router.get("/profiles/{extension_id}")
def get_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_extension(db, extension_id)
    return native_profile_dict(get_or_create_profile(db, extension_id))


@router.put("/profiles/{extension_id}")
def save_profile(
    extension_id: str,
    payload: NativeVoiceProfileUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ensure_extension(db, extension_id)
    row = get_or_create_profile(db, extension_id)
    try:
        update_profile(
            db,
            row,
            {
                "enabled": payload.enabled,
                "mode": "chatgpt_live",
                "style_preset": payload.style_preset,
                "native_voice": payload.native_voice,
                "style_instruction": payload.style_instruction,
                "auto_apply_style": payload.auto_apply_style,
            },
        )
        save_native_tuning(row, payload.native_tuning)
        db.commit()
        db.refresh(row)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_log(
        db,
        category="voice.native_tuning.updated",
        message="ChatGPT 原生语音参数已保存",
        details={
            "extension_id": extension_id,
            "preset": row.style_preset,
            "native_voice": row.native_voice,
            "native_tuning": native_profile_dict(row)["native_tuning"],
        },
    )
    return native_profile_dict(row)


@router.post("/profiles/{extension_id}/apply")
async def apply_profile(extension_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_extension(db, extension_id)
    row = get_or_create_profile(db, extension_id)
    command = await _queue_extension_command(
        db,
        extension_id,
        "director_instruction",
        {
            "text": wrap_director_instruction(style_apply_prompt(row)),
            "auto_send": True,
            "force": False,
            "source": "native_voice_tuning_apply",
        },
        priority=98,
    )
    return {
        "applied": True,
        "command_id": command.id,
        "status": command.status,
        "native_voice": row.native_voice,
        "rendered_instruction": render_native_instruction(row),
        "message": (
            "原生语音参数已发送到当前 ChatGPT Voice 对话。"
            "推荐音色仍需在 ChatGPT 设置中手动选择；本次不会调用 API TTS。"
        ),
    }


@router.post("/profiles/{extension_id}/test")
async def test_profile(
    extension_id: str,
    payload: NativeVoiceTestRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ensure_extension(db, extension_id)
    row = get_or_create_profile(db, extension_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="测试文本不能为空")
    instruction = (
        f"{style_apply_prompt(row)}\n\n"
        "现在只朗读下面这句话一次，不要解释设置，也不要增加其它内容：\n"
        f"{text[:500]}"
    )
    command = await _queue_extension_command(
        db,
        extension_id,
        "director_instruction",
        {
            "text": wrap_director_instruction(instruction),
            "auto_send": True,
            "force": False,
            "source": "native_voice_tuning_test",
        },
        priority=99,
    )
    return {
        "queued": True,
        "command_id": command.id,
        "status": command.status,
        "message": "原生语音测试已发送；不会生成第二路 TTS 音频。",
    }
