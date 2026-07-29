from __future__ import annotations

import os
from typing import Any

from bridge.runtime_diagnostics import event

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
DEFAULT_NO_PROXY_HOSTS = (
    "api.simli.ai",
    ".simli.ai",
    ".livekit.cloud",
    "127.0.0.1",
    "localhost",
)
NETWORK_MODES = {"inherit", "no_proxy", "direct_env"}


def _merge_no_proxy(current: str, values: tuple[str, ...]) -> str:
    rows = [item.strip() for item in current.split(",") if item.strip()]
    seen = {item.lower() for item in rows}
    for value in values:
        if value.lower() not in seen:
            rows.append(value)
            seen.add(value.lower())
    return ",".join(rows)


def apply_simli_network_policy(runtime: Any) -> dict[str, Any]:
    config = getattr(runtime, "config", {}) or {}
    requested = str(config.get("network_mode") or "inherit").strip().lower()
    mode = requested if requested in NETWORK_MODES else "inherit"
    removed: dict[str, str] = {}

    if mode in {"no_proxy", "direct_env"}:
        merged = _merge_no_proxy(
            os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            DEFAULT_NO_PROXY_HOSTS,
        )
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged

    if mode == "direct_env":
        for key in PROXY_ENV_KEYS:
            value = os.environ.pop(key, None)
            if value:
                removed[key] = value

    result = {
        "mode": mode,
        "requested_mode": requested,
        "no_proxy": os.environ.get("NO_PROXY", ""),
        "removed_proxy_env_keys": sorted(removed),
        "tun_routing_required": True,
        "note_zh": (
            "已绕过 Python 环境代理；v2rayN TUN/透明代理仍必须在路由规则中设置 Simli/LiveKit 直连。"
            if mode == "direct_env"
            else "未清除代理环境变量。"
        ),
    }
    runtime.state["network_policy"] = result
    diagnostics = runtime.state.setdefault("diagnostics", {})
    diagnostics["network_policy"] = result
    event("simli_network_policy_applied", **result)
    return result


def install_simli_network_policy(runtime_class: type) -> None:
    if getattr(runtime_class, "_aliver_network_policy_v1", False):
        return

    original_start = runtime_class.start

    async def patched_start(runtime: Any) -> dict[str, Any]:
        # All monitor patches are installed before a runtime starts. Install the
        # GPT_OUT-anchored timeline here so test epochs cannot capture idle events.
        from bridge.simli_link_timeline_v2 import install_link_timeline_v2

        install_link_timeline_v2()
        apply_simli_network_policy(runtime)
        return await original_start(runtime)

    runtime_class.start = patched_start
    runtime_class._aliver_network_policy_v1 = True
