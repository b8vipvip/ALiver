import asyncio

from app.providers.base import ProviderContext
from app.providers.vtube_studio import VTubeStudioProvider


def context(settings: dict) -> ProviderContext:
    return ProviderContext(
        provider_id="provider-vts",
        name="VTube Studio",
        provider_type="vtube_studio",
        api_base_url=None,
        credentials={},
        settings=settings,
    )


def test_vtube_provider_builds_bridge_plan_with_safe_defaults():
    provider = VTubeStudioProvider(context({}))

    test_result = asyncio.run(provider.test_connection())
    session = asyncio.run(provider.create_session({}))

    assert test_result.success is True
    assert test_result.data["live_test_required"] is True
    assert test_result.data["motion_engine_enabled"] is False
    assert session.success is True
    assert session.data["execution_mode"] == "bridge"
    assert session.data["provider_type"] == "vtube_studio"
    assert session.data["config"]["ws_url"] == "ws://127.0.0.1:8001"
    assert session.data["config"]["mouth_input_parameter"] == "VoiceVolume"
    assert session.data["config"]["mouth_output_parameter"] == "ParamMouthOpenY"
    assert session.data["config"]["motion_engine"]["enabled"] is False
    assert session.data["config"]["motion_engine"]["fps"] == 15


def test_vtube_provider_rejects_invalid_websocket_url():
    provider = VTubeStudioProvider(context({"ws_url": "http://127.0.0.1:8001"}))

    result = asyncio.run(provider.create_session({}))

    assert result.success is False
    assert "ws://" in (result.error or "")


def test_vtube_provider_accepts_session_overrides_and_hotkey_mapping():
    provider = VTubeStudioProvider(
        context({"hotkeys": {"wave": "Wave", "unknown": "ignored"}})
    )

    result = asyncio.run(
        provider.create_session(
            {
                "action_cooldown_ms": 500,
                "hotkeys": {"happy": "Happy", "wave": "Wave2"},
                "motion_engine": {
                    "enabled": True,
                    "preset": "lively",
                    "fps": 22,
                    "speech_threshold": 0.12,
                },
            }
        )
    )

    assert result.success is True
    assert result.data["config"]["action_cooldown_ms"] == 500
    assert result.data["config"]["hotkeys"]["happy"] == "Happy"
    assert result.data["config"]["hotkeys"]["wave"] == "Wave2"
    assert result.data["config"]["motion_engine"]["enabled"] is True
    assert result.data["config"]["motion_engine"]["preset"] == "lively"
    assert result.data["config"]["motion_engine"]["fps"] == 22
    assert result.data["config"]["motion_engine"]["speech_threshold"] == 0.12
