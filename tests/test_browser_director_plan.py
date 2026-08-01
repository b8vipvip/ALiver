from __future__ import annotations

from pathlib import Path

import pytest

from app import browser_director_plan_service as planner
from app.browser_plan_voice_guard import _looks_like_director_plan
from app.json_utils import dumps
from app.models import BrowserExtension

ROOT = Path(__file__).resolve().parents[1]


def extension(*, version: str = "0.1.5", metadata: dict | None = None) -> BrowserExtension:
    return BrowserExtension(
        id="extension-test",
        name="ALiver Controller",
        browser_name="Chrome",
        version=version,
        token_hash="hash",
        status="online",
        metadata_json=dumps(metadata or {}),
    )


def valid_metadata(**overrides):
    value = {
        "binding": {"bound": True, "valid": True, "conversationKey": "https://chatgpt.com/c/test"},
        "composer_ready": True,
        "generating": False,
        "live_active": False,
    }
    value.update(overrides)
    return value


def test_browser_prompt_requires_json_and_correlates_request_id():
    prompt = planner.build_browser_plan_prompt(
        request_id="request-123",
        brief="做一场自然聊天直播",
        duration_minutes=45,
        category="chat",
        tone="natural",
        current_settings={"director_name": "测试导演"},
    )

    assert "只输出一个 JSON 对象" in prompt
    assert '"aliver_plan_request_id": "request-123"' in prompt
    assert "不要用语音回答" in prompt
    assert "rundown" in prompt


def test_browser_planner_requires_explicit_valid_binding(monkeypatch):
    monkeypatch.setattr(planner.extension_hub, "is_connected", lambda _: True)

    with pytest.raises(planner.BrowserDirectorPlanError, match="尚未绑定目标 ChatGPT 会话"):
        planner._validate_extension(extension(metadata={"composer_ready": True}))

    with pytest.raises(planner.BrowserDirectorPlanError, match="已经切换"):
        planner._validate_extension(
            extension(
                metadata=valid_metadata(
                    binding={"bound": True, "valid": False, "reason": "已绑定标签页已经切换到另一个会话"}
                )
            )
        )


def test_browser_planner_rejects_voice_mode_and_busy_chatgpt(monkeypatch):
    monkeypatch.setattr(planner.extension_hub, "is_connected", lambda _: True)

    with pytest.raises(planner.BrowserDirectorPlanError, match="正在语音对话"):
        planner._validate_extension(extension(metadata=valid_metadata(live_active=True)))

    with pytest.raises(planner.BrowserDirectorPlanError, match="正在回答"):
        planner._validate_extension(extension(metadata=valid_metadata(generating=True)))


def test_browser_planner_rejects_old_or_offline_extension(monkeypatch):
    monkeypatch.setattr(planner.extension_hub, "is_connected", lambda _: False)
    with pytest.raises(planner.BrowserDirectorPlanError, match="扩展当前离线"):
        planner._validate_extension(extension(metadata=valid_metadata()))

    monkeypatch.setattr(planner.extension_hub, "is_connected", lambda _: True)
    with pytest.raises(planner.BrowserDirectorPlanError, match="版本过旧"):
        planner._validate_extension(extension(version="0.1.4", metadata=valid_metadata()))


def test_planner_json_is_not_sent_to_voice_tts():
    assert _looks_like_director_plan(
        '{"aliver_plan_request_id":"id","director_name":"D","show_title":"T","rundown":[]}'
    )
    assert _looks_like_director_plan(
        '{"director_name":"D","show_title":"T","opening_script":"Hi","rundown":[]}'
    )
    assert not _looks_like_director_plan("大家好，欢迎来到直播间。")


def test_browser_planner_assets_and_console_mode_are_wired():
    manifest = (ROOT / "chrome_extension/manifest.json").read_text(encoding="utf-8")
    entry = (ROOT / "chrome_extension/background_entry.js").read_text(encoding="utf-8")
    background = (ROOT / "chrome_extension/planner_background_patch.js").read_text(encoding="utf-8")
    content = (ROOT / "chrome_extension/planner_content.js").read_text(encoding="utf-8")
    console = (ROOT / "app/static/director_plan_generator.js").read_text(encoding="utf-8")

    assert '"version": "0.1.5"' in manifest
    assert '"service_worker": "background_entry.js"' in manifest
    assert "planner_content.js" in manifest
    assert "planner_background_patch.js" in entry
    assert "message.command_type !== 'plan_generate'" in background
    assert "aliver.plan.probe" in content
    assert "detectLiveActive()" in content
    assert "generation_mode: mode" in console
    assert "browser_chatgpt" in console
    assert "timeoutMs = mode === 'browser_chatgpt' ? 190000" in console
    assert "saveGeneratedPlan(form)" in console
