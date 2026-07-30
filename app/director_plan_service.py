from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.models import AutoDirectorConfig
from app.pro_director_service import ALLOWED_AVATAR_ACTIONS, PRO_DEFAULTS
from app.security import decrypt_json

DEFAULT_BLOCKED_KEYWORDS = ["加微信", "私信领取", "点我头像", "博彩", "赌博", "刷单", "返利"]
CATEGORY_LABELS = {
    "chat": "轻松聊天",
    "knowledge": "知识分享",
    "ai": "AI 科技",
    "story": "故事陪伴",
    "entertainment": "娱乐互动",
    "custom": "主题直播",
}
TONE_LABELS = {
    "natural": "自然亲切",
    "energetic": "活泼有感染力",
    "calm": "温和松弛",
    "professional": "专业清晰",
    "humorous": "轻松幽默",
}


def _clean_text(value: Any, default: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return (text or default)[:limit]


def _clean_list(value: Any, default: list[str], *, limit: int = 12, item_limit: int = 300) -> list[str]:
    if isinstance(value, str):
        items = re.split(r"[\n,，]+", value)
    elif isinstance(value, list):
        items = value
    else:
        items = []
    result: list[str] = []
    for item in items:
        text = _clean_text(item, "", item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result or list(default)


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _slug(name: str, index: int, total: int) -> str:
    if index == 0 or re.search(r"开场|暖场|欢迎", name):
        return "opening"
    if index == total - 1 or re.search(r"收尾|结束|告别", name):
        return "closing"
    if re.search(r"互动|拉活|问答", name):
        return f"engagement-{index + 1}"
    if re.search(r"主题|深聊|分享", name):
        return f"topic-{index + 1}"
    return f"segment-{index + 1}"


def _duration_allocation(weights: list[int], target_seconds: int) -> list[int]:
    count = len(weights)
    minimum = 60
    target_seconds = max(count * minimum, target_seconds)
    remaining = target_seconds - count * minimum
    positive = [max(1, int(item)) for item in weights]
    total_weight = sum(positive)
    raw = [remaining * item / total_weight for item in positive]
    extras = [int(item) for item in raw]
    leftover = remaining - sum(extras)
    order = sorted(range(count), key=lambda index: raw[index] - extras[index], reverse=True)
    for index in order[:leftover]:
        extras[index] += 1
    return [minimum + item for item in extras]


def _base_rundown(brief: str, duration_minutes: int, category: str) -> list[dict[str, Any]]:
    label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["custom"])
    names = ["开场与暖场", "建立互动", "核心主题", "二次拉活", "自然收尾"]
    objectives = [
        "完成问好、说明直播主题，并让观众容易发出第一条评论。",
        "快速识别高价值评论，建立直播间有人说、有人回应的节奏。",
        f"围绕“{brief}”展开连续但不过长的内容，同时穿插回应观众。",
        "互动下降时切换到更容易参与的问题或轻量小游戏。",
        "回顾本场重点、感谢观众，并给直播一个完整结束。",
    ]
    cues = [
        f"自然欢迎观众，说明这是一场{label}直播，并抛出一个所有人都容易回答的问题。",
        "挑选一条容易产生共鸣的评论回应，回答后把问题交给其他观众。",
        f"围绕“{brief}”讲一个明确观点或小故事，控制篇幅，结尾留下接话点。",
        "换一个简单、轻松、无需知识门槛的问题，邀请观众用一句话回答。",
        "简短回顾今天聊到的一个亮点，感谢陪伴，温和告别，不要突然中断。",
    ]
    actions = ["wave", "happy", "thinking", "wave", "happy"]
    durations = _duration_allocation([8, 22, 38, 22, 10], duration_minutes * 60)
    return [
        {
            "id": _slug(name, index, len(names)),
            "name": name,
            "duration_seconds": durations[index],
            "objective": objectives[index],
            "cue": cues[index],
            "avatar_action": actions[index],
        }
        for index, name in enumerate(names)
    ]


def build_local_plan(
    brief: str,
    duration_minutes: int,
    category: str,
    tone: str,
    current_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    brief = _clean_text(brief, "轻松日常聊天", 600)
    category_label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["custom"])
    tone_label = TONE_LABELS.get(tone, TONE_LABELS["natural"])
    title_seed = re.split(r"[。！？!?；;\n]", brief)[0].strip()[:24]
    show_title = f"{title_seed or category_label}直播"
    current = current_settings or {}
    cue_interval = 75 if duration_minutes <= 30 else 90 if duration_minutes <= 90 else 120
    plan = {
        "professional_mode": True,
        "director_name": _clean_text(current.get("director_name"), "ALiver 总导演", 120),
        "show_title": show_title,
        "show_goal": f"围绕“{brief}”完成一场{category_label}直播，保持自然、有回应感，并提升有效互动和停留。",
        "host_persona": f"{tone_label}，表达像真人聊天；先回应再展开，不机械念稿，不连续长篇独白。",
        "audience_profile": f"对{category_label}感兴趣、愿意通过弹幕参与讨论的泛兴趣观众。",
        "director_style": "像专业直播团队的总导演一样少而准地下指令，优先保证节奏、互动公平和安全。",
        "opening_script": f"自然问好，说明今天会围绕“{brief}”轻松聊天，并邀请观众说说自己的看法。",
        "closing_script": "简短回顾本场最有意思的一点，感谢观众陪伴，温和告别并自然结束。",
        "rundown": _base_rundown(brief, duration_minutes, category),
        "min_score": 35,
        "cooldown_seconds": 12,
        "idle_seconds": max(60, min(cue_interval + 30, 240)),
        "max_response_seconds": 25,
        "dedupe_window_seconds": 90,
        "per_user_cooldown_seconds": 120,
        "max_consecutive_replies": 4,
        "segment_cue_interval_seconds": cue_interval,
        "max_queue_age_seconds": 180,
        "event_batch_size": 8,
        "director_temperature": 0.25,
        "blocked_keywords": list(DEFAULT_BLOCKED_KEYWORDS),
        "idle_topics": [
            f"围绕“{brief}”提出一个没有标准答案、容易参与的问题。",
            "回顾刚才的聊天，挑一个最容易继续展开的点，并邀请新进观众参与。",
            "发起一个一句话就能回答的轻松选择题，让直播间重新活跃起来。",
        ],
    }
    return normalize_plan(plan, brief, duration_minutes, current_settings=current_settings)


def normalize_plan(
    raw: dict[str, Any],
    brief: str,
    duration_minutes: int,
    *,
    current_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
    current = {**PRO_DEFAULTS, **(current_settings or {})}
    target_seconds = _clamp_int(duration_minutes, 10, 240, 45) * 60
    raw_rundown = source.get("rundown") if isinstance(source.get("rundown"), list) else []
    segments: list[dict[str, Any]] = []
    for item in raw_rundown[:8]:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"), f"环节 {len(segments) + 1}", 120)
        action = str(item.get("avatar_action") or item.get("action") or "thinking").lower().strip()
        if action not in ALLOWED_AVATAR_ACTIONS:
            action = "thinking"
        seconds = item.get("duration_seconds")
        if seconds is None:
            seconds = float(item.get("duration_minutes") or item.get("minutes") or 5) * 60
        segments.append(
            {
                "name": name,
                "duration_weight": _clamp_int(seconds, 30, 14_400, 300),
                "objective": _clean_text(item.get("objective"), "保持自然互动和直播节奏。", 1000),
                "cue": _clean_text(item.get("cue"), f"围绕“{name}”自然承接话题并邀请观众参与。", 2000),
                "avatar_action": action,
            }
        )
    if len(segments) < 3:
        return build_local_plan(brief, duration_minutes, "custom", "natural", current_settings)
    durations = _duration_allocation([item.pop("duration_weight") for item in segments], target_seconds)
    for index, item in enumerate(segments):
        item["id"] = _slug(item["name"], index, len(segments))
        item["duration_seconds"] = durations[index]
    segments[0]["id"] = "opening"
    segments[-1]["id"] = "closing"

    return {
        "professional_mode": True,
        "director_name": _clean_text(source.get("director_name"), current["director_name"], 120),
        "show_title": _clean_text(source.get("show_title"), current["show_title"], 160),
        "show_goal": _clean_text(source.get("show_goal"), current["show_goal"], 1200),
        "host_persona": _clean_text(source.get("host_persona"), current["host_persona"], 1200),
        "audience_profile": _clean_text(source.get("audience_profile"), current["audience_profile"], 800),
        "director_style": _clean_text(source.get("director_style"), current["director_style"], 1200),
        "opening_script": _clean_text(source.get("opening_script"), current["opening_script"], 2000),
        "closing_script": _clean_text(source.get("closing_script"), current["closing_script"], 2000),
        "rundown": segments,
        "min_score": _clamp_int(source.get("min_score"), 20, 80, 35),
        "cooldown_seconds": _clamp_int(source.get("cooldown_seconds"), 5, 120, 12),
        "idle_seconds": _clamp_int(source.get("idle_seconds"), 30, 900, 120),
        "max_response_seconds": _clamp_int(source.get("max_response_seconds"), 8, 90, 25),
        "dedupe_window_seconds": _clamp_int(source.get("dedupe_window_seconds"), 10, 900, 90),
        "per_user_cooldown_seconds": _clamp_int(source.get("per_user_cooldown_seconds"), 0, 1800, 120),
        "max_consecutive_replies": _clamp_int(source.get("max_consecutive_replies"), 1, 12, 4),
        "segment_cue_interval_seconds": _clamp_int(
            source.get("segment_cue_interval_seconds"), 20, 900, 90
        ),
        "max_queue_age_seconds": _clamp_int(source.get("max_queue_age_seconds"), 30, 1800, 180),
        "event_batch_size": _clamp_int(source.get("event_batch_size"), 3, 20, 8),
        "director_temperature": max(0.0, min(float(source.get("director_temperature") or 0.25), 1.0)),
        "blocked_keywords": _clean_list(
            source.get("blocked_keywords"), DEFAULT_BLOCKED_KEYWORDS, limit=30, item_limit=80
        ),
        "idle_topics": _clean_list(
            source.get("idle_topics"),
            [
                f"围绕“{brief}”提出一个容易回答的问题。",
                "承接刚才的话题，邀请新进观众参与。",
            ],
            limit=10,
            item_limit=500,
        ),
    }


def _chat_completions_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    return cleaned if cleaned.endswith("/chat/completions") else f"{cleaned}/chat/completions"


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("AI generated plan must be a JSON object")
    return value


async def _generate_with_ai(
    *,
    base_url: str,
    model_name: str,
    api_key: str,
    brief: str,
    duration_minutes: int,
    category: str,
    tone: str,
    current_settings: dict[str, Any],
) -> dict[str, Any]:
    system = (
        "你是专业直播团队的节目策划和总导演。请根据用户的一句话需求，生成一套可直接执行的直播导演方案。"
        "方案必须适合由 ChatGPT 语音主播、VTube Studio 数字人和一个后台总导演共同执行。"
        "只输出 JSON，不要 Markdown。JSON 字段必须包含：director_name、show_title、show_goal、host_persona、"
        "audience_profile、director_style、opening_script、closing_script、rundown、min_score、"
        "cooldown_seconds、idle_seconds、max_response_seconds、dedupe_window_seconds、"
        "per_user_cooldown_seconds、max_consecutive_replies、segment_cue_interval_seconds、"
        "max_queue_age_seconds、event_batch_size、director_temperature、blocked_keywords、idle_topics。"
        "rundown 必须为 4 到 8 个环节，每个环节含 name、duration_minutes、objective、cue、avatar_action。"
        "第一个环节必须开场，最后一个必须收尾；avatar_action 只能是 idle、talking、thinking、wave、happy、"
        "surprised、reset。总时长应接近用户要求。导演指令要少而准，避免机械念稿和连续打断主播。"
    )
    user = {
        "brief": brief,
        "duration_minutes": duration_minutes,
        "category": CATEGORY_LABELS.get(category, category),
        "tone": TONE_LABELS.get(tone, tone),
        "current_settings": current_settings,
    }
    payload = {
        "model": model_name,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(_chat_completions_url(base_url), headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    return _parse_json(str(body["choices"][0]["message"]["content"]))


async def generate_director_plan(
    *,
    config: AutoDirectorConfig | None,
    brief: str,
    duration_minutes: int,
    category: str,
    tone: str,
    prefer_ai: bool,
    api_base_url: str | None,
    model_name: str | None,
    api_key: str | None,
    current_settings: dict[str, Any] | None,
) -> dict[str, Any]:
    brief = _clean_text(brief, "轻松日常聊天", 4000)
    duration_minutes = _clamp_int(duration_minutes, 10, 240, 45)
    current_settings = current_settings or {}
    credentials = decrypt_json(config.credentials_encrypted) if config else {}
    resolved_base_url = _clean_text(api_base_url or (config.api_base_url if config else ""), "", 500)
    resolved_model = _clean_text(model_name or (config.model_name if config else ""), "", 200)
    resolved_key = str(api_key or credentials.get("api_key") or "").strip()

    fallback_reason: str | None = None
    source = "local_template"
    raw: dict[str, Any]
    if prefer_ai and resolved_base_url and resolved_model and resolved_key:
        try:
            raw = await _generate_with_ai(
                base_url=resolved_base_url,
                model_name=resolved_model,
                api_key=resolved_key,
                brief=brief,
                duration_minutes=duration_minutes,
                category=category,
                tone=tone,
                current_settings=current_settings,
            )
            source = "ai"
        except Exception as exc:
            fallback_reason = f"AI 生成失败，已自动使用本地专业模板：{type(exc).__name__}: {exc}"
            raw = build_local_plan(brief, duration_minutes, category, tone, current_settings)
    else:
        if prefer_ai:
            fallback_reason = "尚未提供可用的 API Base URL、模型名称和 API Key，已使用本地专业模板。"
        raw = build_local_plan(brief, duration_minutes, category, tone, current_settings)

    plan = normalize_plan(raw, brief, duration_minutes, current_settings=current_settings)
    total_seconds = sum(int(item["duration_seconds"]) for item in plan["rundown"])
    return {
        "source": source,
        "fallback_reason": fallback_reason,
        "plan": plan,
        "summary": {
            "show_title": plan["show_title"],
            "segment_count": len(plan["rundown"]),
            "duration_minutes": round(total_seconds / 60, 1),
            "director_name": plan["director_name"],
        },
    }
