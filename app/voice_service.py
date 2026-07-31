from __future__ import annotations

import asyncio
import hashlib
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bridge_hub import bridge_hub
from app.db import SessionLocal
from app.json_utils import loads
from app.log_service import write_log
from app.models import BridgeAgent, BrowserExtension
from app.security import decrypt_json, encrypt_json
from app.voice_models import VoiceProfile

VOICE_AUDIO_DIR = Path("data") / "voice_audio"
AUDIO_TOKEN_TTL_SECONDS = 900

STYLE_PRESETS: dict[str, dict[str, str]] = {
    "sweet_young": {
        "name": "甜美小女孩感",
        "description": "明亮、甜美、轻快，保持自然，不故意尖叫或过度卖萌。",
        "instruction": (
            "请使用明亮、甜美、轻快、带小女孩感但自然舒适的中文说话方式。"
            "音调可以偏高一点，咬字清楚，语气亲切机灵；不要尖叫，不要过度嗲，不模仿任何具体真人。"
        ),
    },
    "natural_girl": {
        "name": "自然少女",
        "description": "年轻、清爽、自然，适合长时间日常聊天。",
        "instruction": "请使用年轻、清爽、自然的少女感语气，语速正常，情绪有变化但不过度表演。",
    },
    "energetic": {
        "name": "元气活泼",
        "description": "节奏轻快，适合热场、欢迎和趣味互动。",
        "instruction": "请使用元气、活泼、明快的语气，节奏稍快，重点词有轻微强调，但不要连续高声。",
    },
    "gentle": {
        "name": "温柔陪伴",
        "description": "柔和、耐听，适合夜间聊天和故事。",
        "instruction": "请使用温柔、放松、陪伴感强的语气，语速稍慢，停顿自然，声音不要压得过低。",
    },
    "host": {
        "name": "自然主播",
        "description": "清晰、稳定、有互动感，不像念稿。",
        "instruction": "请使用自然主播语气，清楚、有回应感、节奏稳定，像真人聊天，不要播音腔，不要念稿感。",
    },
    "calm": {
        "name": "沉稳知性",
        "description": "克制、清楚，适合技术和知识话题。",
        "instruction": "请使用沉稳、知性、清楚的语气，语速适中，减少夸张语气词，重点结论说得明确。",
    },
}

CHATGPT_NATIVE_VOICES = [
    "Arbor",
    "Breeze",
    "Cove",
    "Ember",
    "Juniper",
    "Maple",
    "Sol",
    "Spruce",
    "Vale",
]

OPENAI_TTS_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]

_audio_lock = threading.RLock()
_audio_tokens: dict[str, dict[str, Any]] = {}
_recent_lock = threading.RLock()
_recent_messages: dict[str, float] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _preset_instruction(preset: str) -> str:
    row = STYLE_PRESETS.get(preset) or STYLE_PRESETS["natural_girl"]
    return row["instruction"]


def get_or_create_profile(db: Session, extension_id: str) -> VoiceProfile:
    row = db.scalar(select(VoiceProfile).where(VoiceProfile.extension_id == extension_id))
    if row is not None:
        return row
    row = VoiceProfile(extension_id=extension_id, style_instruction=_preset_instruction("sweet_young"))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def profile_to_dict(row: VoiceProfile) -> dict[str, Any]:
    credentials = decrypt_json(row.credentials_encrypted)
    return {
        "id": row.id,
        "extension_id": row.extension_id,
        "bridge_id": row.bridge_id,
        "enabled": bool(row.enabled),
        "mode": row.mode,
        "style_preset": row.style_preset,
        "native_voice": row.native_voice,
        "style_instruction": row.style_instruction or _preset_instruction(row.style_preset),
        "auto_apply_style": bool(row.auto_apply_style),
        "auto_mute_chatgpt_tab": bool(row.auto_mute_chatgpt_tab),
        "tts_api_base_url": row.tts_api_base_url,
        "tts_model": row.tts_model,
        "tts_voice": row.tts_voice,
        "tts_speed": round(max(25, min(int(row.tts_speed_percent), 400)) / 100.0, 2),
        "credential_keys": sorted(credentials.keys()),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def update_profile(db: Session, row: VoiceProfile, values: dict[str, Any]) -> VoiceProfile:
    mode = str(values.get("mode", row.mode) or "chatgpt_live").strip()
    if mode not in {"chatgpt_live", "api_tts"}:
        raise ValueError("语音模式只能是 chatgpt_live 或 api_tts")
    preset = str(values.get("style_preset", row.style_preset) or "natural_girl").strip()
    if preset not in STYLE_PRESETS:
        raise ValueError("未知语音风格预设")
    native_voice = str(values.get("native_voice", row.native_voice) or "Maple").strip()
    if native_voice not in CHATGPT_NATIVE_VOICES:
        raise ValueError("未知 ChatGPT 内置音色")
    tts_voice = str(values.get("tts_voice", row.tts_voice) or "shimmer").strip()
    if not tts_voice:
        raise ValueError("TTS voice 不能为空")
    try:
        speed = float(values.get("tts_speed", row.tts_speed_percent / 100.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("TTS 语速必须是数字") from exc
    speed = max(0.25, min(speed, 4.0))

    row.bridge_id = str(values.get("bridge_id") or "").strip() or None
    row.enabled = bool(values.get("enabled", row.enabled))
    row.mode = mode
    row.style_preset = preset
    row.native_voice = native_voice
    custom_instruction = str(values.get("style_instruction") or "").strip()
    row.style_instruction = custom_instruction or _preset_instruction(preset)
    row.auto_apply_style = bool(values.get("auto_apply_style", row.auto_apply_style))
    row.auto_mute_chatgpt_tab = bool(values.get("auto_mute_chatgpt_tab", row.auto_mute_chatgpt_tab))
    row.tts_api_base_url = str(values.get("tts_api_base_url") or "").strip() or None
    row.tts_model = str(values.get("tts_model") or row.tts_model or "gpt-4o-mini-tts").strip()
    row.tts_voice = tts_voice
    row.tts_speed_percent = round(speed * 100)
    if "api_key" in values:
        api_key = str(values.get("api_key") or "").strip()
        current = decrypt_json(row.credentials_encrypted)
        if api_key:
            current["api_key"] = api_key
        elif bool(values.get("clear_api_key")):
            current.pop("api_key", None)
        row.credentials_encrypted = encrypt_json(current)
    db.commit()
    db.refresh(row)
    return row


def voice_style_instruction(db: Session, extension_id: str) -> str:
    row = db.scalar(select(VoiceProfile).where(VoiceProfile.extension_id == extension_id))
    if row is None or not row.enabled or not row.auto_apply_style:
        return ""
    instruction = (row.style_instruction or _preset_instruction(row.style_preset)).strip()
    if not instruction:
        return ""
    return instruction


def decorate_director_content(db: Session, extension_id: str, content: str) -> str:
    instruction = voice_style_instruction(db, extension_id)
    if not instruction:
        return content
    return (
        f"{content.strip()}\n\n"
        "【语音呈现要求】\n"
        f"{instruction}\n"
        "只在说话方式上执行，不要解释或复述这段语音要求。"
    )


def style_apply_prompt(row: VoiceProfile) -> str:
    instruction = (row.style_instruction or _preset_instruction(row.style_preset)).strip()
    return (
        "从下一次回答开始，请持续采用下面的语音呈现方式，直到我再次修改：\n"
        f"{instruction}\n"
        "不要在回答中说明你正在执行语音风格设置。"
    )


def _speech_url(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/audio/speech"):
        return clean
    return f"{clean}/audio/speech"


def _choose_bridge(db: Session, requested: str | None) -> str:
    if requested and bridge_hub.is_connected(requested):
        return requested
    rows = db.scalars(select(BridgeAgent).order_by(BridgeAgent.last_seen_at.desc())).all()
    connected = next((row for row in rows if bridge_hub.is_connected(row.id)), None)
    if connected is None:
        raise RuntimeError("没有在线 Windows Bridge，无法播放 API TTS")
    return connected.id


def _register_audio(path: Path) -> tuple[str, str]:
    audio_id = str(uuid4())
    token = secrets.token_urlsafe(32)
    with _audio_lock:
        _audio_tokens[audio_id] = {
            "token": token,
            "path": path,
            "expires_at": time.time() + AUDIO_TOKEN_TTL_SECONDS,
        }
    return audio_id, token


def resolve_audio_file(audio_id: str, token: str) -> Path | None:
    cleanup_audio_cache()
    with _audio_lock:
        row = _audio_tokens.get(audio_id)
        if not row or not secrets.compare_digest(str(row.get("token") or ""), str(token or "")):
            return None
        path = Path(row["path"])
        return path if path.exists() else None


def cleanup_audio_cache() -> None:
    now = time.time()
    expired: list[Path] = []
    with _audio_lock:
        for audio_id, row in list(_audio_tokens.items()):
            if float(row.get("expires_at") or 0) <= now:
                expired.append(Path(row["path"]))
                _audio_tokens.pop(audio_id, None)
    for path in expired:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _message_seen(extension_id: str, message_id: str, text: str) -> bool:
    fingerprint = hashlib.sha256(f"{extension_id}|{message_id}|{text}".encode("utf-8")).hexdigest()
    now = time.time()
    with _recent_lock:
        for key, at in list(_recent_messages.items()):
            if now - at > 3600:
                _recent_messages.pop(key, None)
        if fingerprint in _recent_messages:
            return True
        _recent_messages[fingerprint] = now
        return False


async def synthesize_and_play(
    profile: dict[str, Any],
    *,
    text: str,
    bridge_id: str,
    source: str,
) -> dict[str, Any]:
    api_key = str((profile.get("credentials") or {}).get("api_key") or "").strip()
    base_url = str(profile.get("tts_api_base_url") or "").strip()
    if not base_url:
        raise RuntimeError("API TTS 模式尚未配置 API Base URL")
    if not api_key:
        raise RuntimeError("API TTS 模式尚未配置 API Key")
    clean_text = text.strip()
    if not clean_text:
        raise RuntimeError("没有可合成的回答文本")
    clean_text = clean_text[:4096]
    payload: dict[str, Any] = {
        "model": str(profile.get("tts_model") or "gpt-4o-mini-tts"),
        "voice": str(profile.get("tts_voice") or "shimmer"),
        "input": clean_text,
        "response_format": "wav",
        "speed": float(profile.get("tts_speed") or 1.0),
    }
    instruction = str(profile.get("style_instruction") or "").strip()
    if instruction:
        payload["instructions"] = instruction
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(_speech_url(base_url), headers=headers, json=payload)
        response.raise_for_status()
        audio_bytes = response.content
    if len(audio_bytes) < 44:
        raise RuntimeError("TTS 接口没有返回有效 WAV 音频")
    VOICE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = VOICE_AUDIO_DIR / f"voice-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.wav"
    path.write_bytes(audio_bytes)
    audio_id, token = _register_audio(path)
    result = await bridge_hub.send_command(
        bridge_id,
        "audio.gpt_out.play_tts",
        {
            "audio_path": f"/api/voice/audio/{audio_id}?token={token}",
            "source": source,
            "delete_after": True,
        },
        timeout=180.0,
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "Bridge 播放 TTS 失败"))
    return {
        "played": True,
        "bridge_id": bridge_id,
        "characters": len(clean_text),
        "synthesis_ms": round((time.monotonic() - started) * 1000, 1),
        "bridge_result": result.get("data") or {},
    }


async def handle_assistant_completed(extension_id: str, data: dict[str, Any]) -> None:
    text = str(data.get("text") or "").strip()
    message_id = str(data.get("message_id") or "").strip()
    if not text or _message_seen(extension_id, message_id, text):
        return
    with SessionLocal() as db:
        row = db.scalar(select(VoiceProfile).where(VoiceProfile.extension_id == extension_id))
        if row is None or not row.enabled or row.mode != "api_tts":
            return
        credentials = decrypt_json(row.credentials_encrypted)
        profile = {**profile_to_dict(row), "credentials": credentials}
        bridge_id = _choose_bridge(db, row.bridge_id)
        write_log(
            db,
            category="voice.assistant.completed",
            message="捕获到 ChatGPT 完整回答，准备生成 API TTS",
            bridge_id=bridge_id,
            details={
                "extension_id": extension_id,
                "message_id": message_id,
                "characters": len(text),
                "voice": row.tts_voice,
                "model": row.tts_model,
            },
        )
    try:
        result = await synthesize_and_play(
            profile,
            text=text,
            bridge_id=bridge_id,
            source="assistant_completed",
        )
    except Exception as exc:
        with SessionLocal() as db:
            write_log(
                db,
                category="voice.tts.failed",
                level="ERROR",
                message=f"API TTS 生成或播放失败：{type(exc).__name__}: {exc}",
                bridge_id=bridge_id,
                details={"extension_id": extension_id, "message_id": message_id},
            )
        return
    with SessionLocal() as db:
        write_log(
            db,
            category="voice.tts.played",
            message="API TTS 已生成并输出到 GPT_OUT",
            bridge_id=bridge_id,
            latency_ms=int(result.get("synthesis_ms") or 0),
            details={"extension_id": extension_id, "message_id": message_id, **result},
        )


async def test_voice_profile(db: Session, row: VoiceProfile, text: str) -> dict[str, Any]:
    if row.mode != "api_tts":
        raise ValueError("当前不是 API TTS 模式；ChatGPT Live 模式请使用“应用到当前对话”测试语音风格")
    credentials = decrypt_json(row.credentials_encrypted)
    profile = {**profile_to_dict(row), "credentials": credentials}
    bridge_id = _choose_bridge(db, row.bridge_id)
    return await synthesize_and_play(
        profile,
        text=text or "你好呀，这是 ALiver 甜美语音的第一版测试。",
        bridge_id=bridge_id,
        source="voice_profile_test",
    )


def profile_catalog() -> dict[str, Any]:
    return {
        "style_presets": STYLE_PRESETS,
        "chatgpt_native_voices": CHATGPT_NATIVE_VOICES,
        "tts_voices": OPENAI_TTS_VOICES,
        "modes": {
            "chatgpt_live": "ChatGPT Live 原生语音：可调整语气、语速和表达风格；内置音色仍需在 ChatGPT 设置中选择。",
            "api_tts": "ALiver API TTS：捕获 ChatGPT 完整文字回答后生成指定音色并输出到 GPT_OUT。",
        },
    }
