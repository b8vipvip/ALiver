from __future__ import annotations

import time
from typing import Any, ClassVar

from app.providers.base import AvatarProvider, ProviderResult


class DomesticRealtimeProvider(AvatarProvider):
    """Configuration-first adapter for domestic realtime avatar vendors.

    The provider validates encrypted credentials and emits a Bridge execution plan.
    Vendor RTC/SDK media connectors are intentionally marked as reserved until the
    vendor account, avatar asset and SDK package are available for integration tests.
    """

    execution_mode = "bridge"
    vendor_name: ClassVar[str]
    docs_url: ClassVar[str]
    required_credentials: ClassVar[tuple[str, ...]] = ()
    required_any_settings: ClassVar[tuple[str, ...]] = ()
    default_settings: ClassVar[dict[str, Any]] = {}

    def _runtime_config(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = {**self.default_settings, **self.context.settings, **(overrides or {})}
        credentials = {
            key: value
            for key, value in self.context.credentials.items()
            if value is not None and str(value).strip()
        }
        missing_credentials = [key for key in self.required_credentials if not credentials.get(key)]
        if missing_credentials:
            raise ValueError(
                "Missing credentials: " + ", ".join(sorted(missing_credentials))
            )
        if self.required_any_settings and not any(settings.get(key) for key in self.required_any_settings):
            raise ValueError(
                "At least one setting is required: "
                + ", ".join(sorted(self.required_any_settings))
            )
        return {
            "provider_type": self.provider_type,
            "vendor_name": self.vendor_name,
            "api_base_url": self.context.api_base_url,
            "credentials": credentials,
            "settings": settings,
            "docs_url": self.docs_url,
            "adapter_stage": "reserved_bridge_connector",
        }

    async def test_connection(self) -> ProviderResult:
        started = time.perf_counter()
        try:
            config = self._runtime_config()
        except ValueError as exc:
            return ProviderResult(success=False, error=str(exc))
        return ProviderResult(
            success=True,
            latency_ms=round((time.perf_counter() - started) * 1000),
            data={
                "message_zh": (
                    f"{self.vendor_name} 配置字段校验通过。"
                    "当前为预留适配层，完整 RTC/SDK 联通测试将在对应 Bridge connector 接入后启用。"
                ),
                "validation_only": True,
                "adapter_stage": config["adapter_stage"],
                "docs_url": self.docs_url,
                "settings_keys": sorted(config["settings"].keys()),
            },
        )

    async def create_session(self, overrides: dict[str, Any]) -> ProviderResult:
        try:
            config = self._runtime_config(overrides)
        except ValueError as exc:
            return ProviderResult(success=False, error=str(exc))
        return ProviderResult(
            success=True,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.start_session",
                "provider_type": self.provider_type,
                "config": config,
            },
        )

    async def stop_session(
        self,
        external_session_id: str | None,
        session_data: dict[str, Any],
    ) -> ProviderResult:
        return ProviderResult(
            success=True,
            external_session_id=external_session_id,
            data={
                "execution_mode": "bridge",
                "command_type": "provider.stop_session",
                "provider_type": self.provider_type,
            },
        )


class TencentDigitalHumanProvider(DomesticRealtimeProvider):
    provider_type = "tencent_digital_human"
    vendor_name = "腾讯云智能数智人"
    docs_url = "https://cloud.tencent.com/document/product/1240/130451"
    required_credentials = ("app_key", "access_token")
    required_any_settings = ("virtualman_project_id", "asset_virtualman_key")
    default_settings = {
        "stream_protocol": "webrtc",
        "driver_mode": "audio",
        "audio_format": "pcm_s16le_16000_mono",
        "window_title": "ALiver Tencent Digital Human",
    }


class AliyunAvatarProvider(DomesticRealtimeProvider):
    provider_type = "aliyun_avatar"
    vendor_name = "阿里云万相数字人"
    docs_url = "https://help.aliyun.com/zh/model-studio/avatar-dialog-api"
    required_credentials = ("dashscope_api_key",)
    required_any_settings = ("avatar_id", "avatar_code")
    default_settings = {
        "model": "avatar-dialog",
        "stream_protocol": "aliyun_rtc",
        "driver_mode": "audio",
        "audio_format": "pcm_s16le_16000_mono",
        "window_title": "ALiver Aliyun Avatar",
    }


class BaiduXilingProvider(DomesticRealtimeProvider):
    provider_type = "baidu_xiling"
    vendor_name = "百度曦灵数字人"
    docs_url = "https://cloud.baidu.com/doc/AI_DH/s/Kmk6vt4dh"
    required_credentials = ("app_id", "app_key")
    required_any_settings = ("digital_human_id", "asset_id")
    default_settings = {
        "render_mode": "windows_sdk",
        "driver_mode": "audio",
        "audio_format": "pcm_s16le_16000_mono",
        "window_title": "ALiver Baidu Xiling",
    }
