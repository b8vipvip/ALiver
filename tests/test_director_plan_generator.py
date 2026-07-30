import pytest

from app import director_plan_service


def register_extension(client):
    response = client.post(
        "/api/director/extensions/register",
        json={"name": "Plan Generator Chrome", "browser_name": "Chrome", "version": "0.1.1"},
    )
    assert response.status_code == 201
    return response.json()


def test_local_plan_endpoint_generates_complete_exact_duration_plan(client):
    extension = register_extension(client)
    response = client.post(
        "/api/auto-director/plan/generate",
        json={
            "extension_id": extension["extension_id"],
            "brief": "做一场关于 AI 如何帮助普通人的轻松聊天直播，多回应观众问题。",
            "duration_minutes": 45,
            "category": "ai",
            "tone": "natural",
            "prefer_ai": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "local_template"
    plan = body["plan"]
    assert plan["show_title"]
    assert plan["opening_script"]
    assert plan["closing_script"]
    assert len(plan["rundown"]) >= 4
    assert plan["rundown"][0]["id"] == "opening"
    assert plan["rundown"][-1]["id"] == "closing"
    assert sum(item["duration_seconds"] for item in plan["rundown"]) == 45 * 60


@pytest.mark.asyncio
async def test_ai_plan_is_normalized_before_return(monkeypatch):
    async def fake_generate(**kwargs):
        return {
            "director_name": "测试总导演",
            "show_title": "AI 生活实验室",
            "show_goal": "让观众获得实用信息并愿意互动",
            "host_persona": "自然、清晰、会追问",
            "audience_profile": "普通科技爱好者",
            "director_style": "少而准地下指令",
            "opening_script": "欢迎大家并介绍主题",
            "closing_script": "总结并感谢观众",
            "rundown": [
                {
                    "name": "开场",
                    "duration_minutes": 2,
                    "objective": "欢迎观众",
                    "cue": "问好并提问",
                    "avatar_action": "wave",
                },
                {
                    "name": "案例分享",
                    "duration_minutes": 8,
                    "objective": "讲实用案例",
                    "cue": "讲一个具体例子",
                    "avatar_action": "invalid-action",
                },
                {
                    "name": "互动问答",
                    "duration_minutes": 5,
                    "objective": "回答观众",
                    "cue": "挑选高质量问题",
                    "avatar_action": "thinking",
                },
                {
                    "name": "收尾",
                    "duration_minutes": 2,
                    "objective": "完整结束",
                    "cue": "感谢并告别",
                    "avatar_action": "happy",
                },
            ],
            "min_score": 999,
            "cooldown_seconds": 1,
            "idle_topics": ["聊聊大家最常用的 AI 工具"],
        }

    monkeypatch.setattr(director_plan_service, "_generate_with_ai", fake_generate)
    result = await director_plan_service.generate_director_plan(
        config=None,
        brief="AI 如何帮助普通人的生活",
        duration_minutes=30,
        category="ai",
        tone="professional",
        prefer_ai=True,
        api_base_url="https://example.test/v1",
        model_name="director-model",
        api_key="secret",
        current_settings={},
    )
    assert result["source"] == "ai"
    plan = result["plan"]
    assert sum(item["duration_seconds"] for item in plan["rundown"]) == 30 * 60
    assert plan["rundown"][1]["avatar_action"] == "thinking"
    assert plan["min_score"] == 80
    assert plan["cooldown_seconds"] == 5


@pytest.mark.asyncio
async def test_missing_ai_credentials_falls_back_without_failing():
    result = await director_plan_service.generate_director_plan(
        config=None,
        brief="轻松故事陪伴直播",
        duration_minutes=20,
        category="story",
        tone="calm",
        prefer_ai=True,
        api_base_url=None,
        model_name=None,
        api_key=None,
        current_settings={},
    )
    assert result["source"] == "local_template"
    assert "API Base URL" in result["fallback_reason"]
    assert result["plan"]["rundown"]
