from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.director_plan_service import (
    CATEGORY_LABELS,
    TONE_LABELS,
    _parse_json,
    normalize_plan,
)
from app.director_service import dispatch_command
from app.extension_hub import extension_hub
from app.json_utils import dumps, loads
from app.models import BrowserExtension, DirectorCommand

MIN_EXTENSION_VERSION = (0, 1, 5)
DEFAULT_TIMEOUT_SECONDS = 170.0
_ACTIVE_PLAN_COUNTS: dict[str, int] = {}


class BrowserDirectorPlanError(RuntimeError):
    """Raised when the bound ChatGPT browser planner cannot complete."""


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in numbers[:3]) or (0,)


def begin_browser_plan(extension_id: str) -> None:
    _ACTIVE_PLAN_COUNTS[extension_id] = _ACTIVE_PLAN_COUNTS.get(extension_id, 0) + 1


def end_browser_plan(extension_id: str) -> None:
    count = _ACTIVE_PLAN_COUNTS.get(extension_id, 0) - 1
    if count > 0:
        _ACTIVE_PLAN_COUNTS[extension_id] = count
    else:
        _ACTIVE_PLAN_COUNTS.pop(extension_id, None)


def is_browser_plan_active(extension_id: str) -> bool:
    return _ACTIVE_PLAN_COUNTS.get(extension_id, 0) > 0


def build_browser_plan_prompt(
    *,
    request_id: str,
    brief: str,
    duration_minutes: int,
    category: str,
    tone: str,
    current_settings: dict[str, Any],
) -> str:
    request = {
        "aliver_plan_request_id": request_id,
        "brief": brief,
        "duration_minutes": duration_minutes,
        "category": CATEGORY_LABELS.get(category, category),
        "tone": TONE_LABELS.get(tone, tone),
        "current_settings": current_settings,
    }
    return (
        "【ALiver 直播方案策划任务】\n"
        "这不是直播中的导演口播，也不要用语音回答。你现在是专业直播团队的节目策划和总导演，"
        "请根据下面的需求生成一套可直接写入 ALiver 导演中心的完整方案。\n\n"
        "只输出一个 JSON 对象，不要 Markdown、解释、前后缀或代码围栏。"
        "JSON 顶层必须原样包含 aliver_plan_request_id，并包含：director_name、show_title、show_goal、"
        "host_persona、audience_profile、director_style、opening_script、closing_script、rundown、"
        "min_score、cooldown_seconds、idle_seconds、max_response_seconds、dedupe_window_seconds、"
        "per_user_cooldown_seconds、max_consecutive_replies、segment_cue_interval_seconds、"
        "max_queue_age_seconds、event_batch_size、director_temperature、blocked_keywords、idle_topics。\n"
        "rundown 必须有 4 到 8 个环节；每个环节包含 name、duration_minutes、objective、cue、"
        "avatar_action。第一个环节必须是开场，最后一个必须是收尾。avatar_action 只能为 idle、"
        "talking、thinking、wave、happy、surprised、reset。总时长应接近要求。"
        "导演提示要少而准，不机械念稿，不连续打断主播。\n\n"
        f"输入数据：{json.dumps(request, ensure_ascii=False)}"
    )


def _validate_extension(extension: BrowserExtension) -> None:
    if not extension_hub.is_connected(extension.id):
        raise BrowserDirectorPlanError("ALiver Controller 扩展当前离线，请先重新连接扩展。")
    if _version_tuple(extension.version) < MIN_EXTENSION_VERSION:
        raise BrowserDirectorPlanError(
            "当前 ALiver Controller 版本过旧。请更新并重新加载 Chrome 扩展 0.1.5 或更高版本。"
        )
    metadata = loads(extension.metadata_json, {})
    binding = metadata.get("binding") if isinstance(metadata.get("binding"), dict) else {}
    if binding and not bool(binding.get("valid")):
        raise BrowserDirectorPlanError(str(binding.get("reason") or "当前绑定的 ChatGPT 会话已失效。"))
    if bool(metadata.get("live_active")):
        raise BrowserDirectorPlanError("当前绑定的 ChatGPT 正在语音对话。请先结束语音模式，再生成直播方案。")
    if not bool(metadata.get("composer_ready")):
        raise BrowserDirectorPlanError("当前绑定的 ChatGPT 输入框未就绪，请刷新页面后重试。")
    if bool(metadata.get("generating")):
        raise BrowserDirectorPlanError("当前 ChatGPT 正在回答，请等待回答结束后再生成方案。")


async def _wait_for_command(
    db: Session,
    command_id: str,
    *,
    timeout_seconds: float,
) -> DirectorCommand:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.scalar(
            select(DirectorCommand)
            .where(DirectorCommand.id == command_id)
            .execution_options(populate_existing=True)
        )
        db.commit()
        if row is None:
            raise BrowserDirectorPlanError("浏览器策划命令记录丢失。")
        if row.status == "completed":
            return row
        if row.status == "failed":
            raise BrowserDirectorPlanError(row.error_message or "ChatGPT 浏览器策划失败。")
        await asyncio.sleep(0.25)

    row = db.get(DirectorCommand, command_id)
    if row is not None and row.status not in {"completed", "failed"}:
        row.status = "failed"
        row.error_message = f"等待 ChatGPT 策划回答超过 {int(timeout_seconds)} 秒"
        db.commit()
    raise BrowserDirectorPlanError(f"等待 ChatGPT 策划回答超过 {int(timeout_seconds)} 秒。")


async def generate_plan_with_bound_chatgpt(
    *,
    db: Session,
    extension: BrowserExtension,
    brief: str,
    duration_minutes: int,
    category: str,
    tone: str,
    current_settings: dict[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    _validate_extension(extension)
    request_id = uuid4().hex
    prompt = build_browser_plan_prompt(
        request_id=request_id,
        brief=brief,
        duration_minutes=duration_minutes,
        category=category,
        tone=tone,
        current_settings=current_settings,
    )
    command = DirectorCommand(
        extension_id=extension.id,
        command_type="plan_generate",
        payload_json=dumps(
            {
                "text": prompt,
                "request_id": request_id,
                "timeout_ms": int(timeout_seconds * 1000),
                "require_voice_inactive": True,
            }
        ),
        status="queued",
        priority=100,
    )
    db.add(command)
    db.commit()
    db.refresh(command)

    begin_browser_plan(extension.id)
    try:
        sent = await dispatch_command(db, command)
        if not sent:
            raise BrowserDirectorPlanError("ALiver Controller 扩展当前未连接，策划命令没有发送。")
        completed = await _wait_for_command(db, command.id, timeout_seconds=timeout_seconds)
        result = loads(completed.result_json, {})
        response_text = str(result.get("response_text") or "").strip()
        if not response_text:
            raise BrowserDirectorPlanError("ChatGPT 已完成回答，但扩展没有读取到方案文本。")
        raw = _parse_json(response_text)
        returned_request_id = str(raw.get("aliver_plan_request_id") or "").strip()
        if returned_request_id and returned_request_id != request_id:
            raise BrowserDirectorPlanError("收到的 ChatGPT 方案与本次请求不匹配，请重新生成。")
        plan = normalize_plan(
            raw,
            brief,
            duration_minutes,
            current_settings=current_settings,
        )
        total_seconds = sum(int(item["duration_seconds"]) for item in plan["rundown"])
        return {
            "source": "browser_chatgpt",
            "fallback_reason": None,
            "plan": plan,
            "summary": {
                "show_title": plan["show_title"],
                "segment_count": len(plan["rundown"]),
                "duration_minutes": round(total_seconds / 60, 1),
                "director_name": plan["director_name"],
            },
            "browser": {
                "command_id": completed.id,
                "request_id": request_id,
                "elapsed_ms": result.get("elapsed_ms"),
                "url": result.get("url"),
                "title": result.get("title"),
            },
        }
    finally:
        end_browser_plan(extension.id)
