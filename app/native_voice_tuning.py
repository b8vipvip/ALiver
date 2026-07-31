from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.json_utils import dumps, loads
from app.voice_models import VoiceProfile

DEFAULT_NATIVE_TUNING: dict[str, float] = {
    "pitch": 68.0,
    "pace": 104.0,
    "sweetness": 72.0,
    "brightness": 70.0,
    "energy": 58.0,
    "warmth": 48.0,
    "clarity": 82.0,
    "expressiveness": 64.0,
    "pause": 42.0,
}

TUNING_LIMITS: dict[str, tuple[float, float]] = {
    "pitch": (0.0, 100.0),
    "pace": (75.0, 135.0),
    "sweetness": (0.0, 100.0),
    "brightness": (0.0, 100.0),
    "energy": (0.0, 100.0),
    "warmth": (0.0, 100.0),
    "clarity": (0.0, 100.0),
    "expressiveness": (0.0, 100.0),
    "pause": (0.0, 100.0),
}


def normalize_native_tuning(value: Any) -> dict[str, float]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, float] = {}
    for key, default in DEFAULT_NATIVE_TUNING.items():
        minimum, maximum = TUNING_LIMITS[key]
        try:
            number = float(source.get(key, default))
        except (TypeError, ValueError):
            number = default
        result[key] = round(max(minimum, min(number, maximum)), 2)
    return result


def profile_native_tuning(row: VoiceProfile) -> dict[str, float]:
    return normalize_native_tuning(loads(row.native_tuning_json, {}))


def save_native_tuning(row: VoiceProfile, tuning: Any) -> dict[str, float]:
    normalized = normalize_native_tuning(tuning)
    row.native_tuning_json = dumps(normalized)
    row.tts_speed_percent = round(normalized["pace"])
    return normalized


def _level(value: float, *, low: str, medium: str, high: str) -> str:
    if value < 35:
        return low
    if value > 68:
        return high
    return medium


def render_native_instruction(row: VoiceProfile) -> str:
    tuning = profile_native_tuning(row)
    custom = str(row.style_instruction or "").strip()
    pitch = _level(
        tuning["pitch"],
        low="声线听感稍低、放松，不要压嗓",
        medium="声线保持自然中高区，避免刻意抬高",
        high="声线听感明亮偏高，带年轻感，但不要尖细或变成卡通腔",
    )
    sweetness = _level(
        tuning["sweetness"],
        low="减少撒娇和甜腻感",
        medium="保持自然亲切、略带甜感",
        high="增加甜美、轻巧和亲近感，但不要嗲声嗲气",
    )
    brightness = _level(
        tuning["brightness"],
        low="音色偏柔和暗一点",
        medium="音色清爽自然",
        high="音色更明亮通透，辅音清晰",
    )
    energy = _level(
        tuning["energy"],
        low="整体能量放松克制",
        medium="保持自然聊天能量",
        high="整体更有元气和回应感，但不要持续大声",
    )
    warmth = _level(
        tuning["warmth"],
        low="减少厚重和低频感",
        medium="保留适度温暖感",
        high="增加温柔、陪伴和包裹感",
    )
    clarity = _level(
        tuning["clarity"],
        low="咬字可以更口语化",
        medium="咬字自然清楚",
        high="咬字清晰利落，句尾不要含糊",
    )
    expression = _level(
        tuning["expressiveness"],
        low="情绪变化克制",
        medium="根据内容自然变化情绪",
        high="加强笑意、惊喜和重点词变化，但不要表演过度",
    )
    pause = _level(
        tuning["pause"],
        low="减少长停顿，衔接紧凑",
        medium="停顿自然，像真人聊天",
        high="适当增加短停顿，让语句更轻松、有呼吸感",
    )
    pace = int(round(tuning["pace"]))
    if pace <= 90:
        pace_text = "语速明显偏慢"
    elif pace <= 99:
        pace_text = "语速稍慢"
    elif pace <= 108:
        pace_text = "语速自然偏轻快"
    elif pace <= 118:
        pace_text = "语速较快但必须保持清楚"
    else:
        pace_text = "语速快速紧凑，但不要吞字"

    parts = [
        custom,
        (
            "请在当前 ChatGPT Live 原生语音中持续采用以下说话方式："
            f"{pitch}；{sweetness}；{brightness}；{energy}；{warmth}；"
            f"{clarity}；{expression}；{pause}；{pace_text}。"
        ),
        (
            "这是表达与声线听感要求，不要在回答中解释这些参数；"
            "不要模仿任何具体真人，也不要用尖叫、电子音或夸张卡通腔。"
        ),
    ]
    return "\n".join(part for part in parts if part).strip()


def native_profile_dict(row: VoiceProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "extension_id": row.extension_id,
        "enabled": bool(row.enabled),
        "mode": "chatgpt_live",
        "style_preset": row.style_preset,
        "native_voice": row.native_voice,
        "style_instruction": row.style_instruction or "",
        "native_tuning": profile_native_tuning(row),
        "rendered_instruction": render_native_instruction(row),
        "auto_apply_style": bool(row.auto_apply_style),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def install_native_voice_patch() -> None:
    from app import voice_service

    if getattr(voice_service, "_aliver_native_voice_tuning_v2", False):
        return
    original_profile_to_dict = voice_service.profile_to_dict

    def profile_to_dict(row: VoiceProfile) -> dict[str, Any]:
        value = original_profile_to_dict(row)
        value["native_tuning"] = profile_native_tuning(row)
        value["rendered_instruction"] = render_native_instruction(row)
        return value

    def voice_style_instruction(db: Session, extension_id: str) -> str:
        row = db.scalar(select(VoiceProfile).where(VoiceProfile.extension_id == extension_id))
        if row is None or not row.enabled or not row.auto_apply_style:
            return ""
        return render_native_instruction(row)

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
        return (
            "从下一次回答开始，请在当前语音对话中持续采用下面的原生语音表达方式，"
            "直到我再次修改：\n"
            f"{render_native_instruction(row)}\n"
            "不要在回答中说明你正在执行语音设置。"
        )

    voice_service.profile_to_dict = profile_to_dict
    voice_service.voice_style_instruction = voice_style_instruction
    voice_service.decorate_director_content = decorate_director_content
    voice_service.style_apply_prompt = style_apply_prompt
    voice_service._aliver_native_voice_tuning_v2 = True
