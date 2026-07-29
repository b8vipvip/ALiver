import asyncio

from app.providers.base import ProviderContext
from app.providers.domestic_realtime import (
    AliyunAvatarProvider,
    BaiduXilingProvider,
    TencentDigitalHumanProvider,
)


def context(provider_type: str, credentials: dict, settings: dict) -> ProviderContext:
    return ProviderContext(
        provider_id="provider-1",
        name="备用供应商",
        provider_type=provider_type,
        api_base_url=None,
        credentials=credentials,
        settings=settings,
    )


def test_tencent_adapter_validates_and_builds_bridge_plan():
    provider = TencentDigitalHumanProvider(
        context(
            "tencent_digital_human",
            {"app_key": "app", "access_token": "token"},
            {"virtualman_project_id": "project"},
        )
    )

    test_result = asyncio.run(provider.test_connection())
    session = asyncio.run(provider.create_session({}))

    assert test_result.success is True
    assert test_result.data["validation_only"] is True
    assert session.success is True
    assert session.data["execution_mode"] == "bridge"
    assert session.data["provider_type"] == "tencent_digital_human"
    assert session.data["config"]["adapter_stage"] == "reserved_bridge_connector"


def test_aliyun_adapter_requires_avatar_asset():
    provider = AliyunAvatarProvider(
        context(
            "aliyun_avatar",
            {"dashscope_api_key": "key"},
            {},
        )
    )

    result = asyncio.run(provider.test_connection())

    assert result.success is False
    assert "avatar" in (result.error or "")


def test_baidu_adapter_rejects_missing_credentials():
    provider = BaiduXilingProvider(
        context(
            "baidu_xiling",
            {"app_id": "app"},
            {"digital_human_id": "digital-human"},
        )
    )

    result = asyncio.run(provider.create_session({}))

    assert result.success is False
    assert "app_key" in (result.error or "")
