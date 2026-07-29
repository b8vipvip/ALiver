from __future__ import annotations

from typing import Any

DOMESTIC_PROVIDER_TYPES = {
    "tencent_digital_human": "腾讯云智能数智人",
    "aliyun_avatar": "阿里云万相数字人",
    "baidu_xiling": "百度曦灵数字人",
}


def start_domestic_provider(payload: dict[str, Any]) -> dict[str, Any]:
    provider_type = str(payload.get("provider_type") or "")
    if provider_type not in DOMESTIC_PROVIDER_TYPES:
        raise ValueError(f"Unsupported domestic provider: {provider_type}")
    plan = payload.get("provider_plan") or {}
    config = plan.get("config") or {}
    vendor = DOMESTIC_PROVIDER_TYPES[provider_type]
    return {
        "status": "awaiting_manual",
        "external_session_id": str(payload.get("session_id") or "") or None,
        "provider_type": provider_type,
        "vendor_name": vendor,
        "adapter_stage": "reserved_bridge_connector",
        "message_zh": (
            f"{vendor} 已注册到 ALiver Provider/Bridge 适配层。"
            "当前版本完成配置校验与会话编排预留，尚未加载厂商 RTC/端渲染 SDK，"
            "因此不会伪装成已建立实时媒体连接。"
        ),
        "config_received": {
            "api_base_url": config.get("api_base_url"),
            "settings": config.get("settings") or {},
            "credential_keys": sorted((config.get("credentials") or {}).keys()),
            "docs_url": config.get("docs_url"),
        },
    }


def stop_domestic_provider(payload: dict[str, Any]) -> dict[str, Any]:
    provider_type = str(payload.get("provider_type") or "")
    vendor = DOMESTIC_PROVIDER_TYPES.get(provider_type, provider_type or "国内数字人供应商")
    return {
        "status": "ended",
        "provider_type": provider_type,
        "message_zh": f"{vendor} 预留会话已在 ALiver 本地结束。",
    }
