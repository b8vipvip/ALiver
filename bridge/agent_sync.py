from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from bridge import agent
from bridge.control_channel import install_bridge_control_guard
from bridge.domestic_provider_scaffolds import (
    DOMESTIC_PROVIDER_TYPES,
    start_domestic_provider,
    stop_domestic_provider,
)
from bridge.douyin_ocr_result_patch import install_douyin_ocr_result_patch
from bridge.douyin_region_occlusion_patch import install_douyin_region_occlusion_patch
from bridge.douyin_scan_logging_patch import install_douyin_scan_logging_patch
from bridge.douyin_three_channel_config_patch import install_douyin_three_channel_config_patch
from bridge.douyin_three_channel_patch import install_douyin_three_channel_patch
from bridge.douyin_validation_fix import install_douyin_validation_fix
from bridge.douyin_visible_runtime_patch import install_visible_collector_runtime_patch
from bridge.douyin_wgc_hwnd_patch import install_douyin_wgc_hwnd_patch
from bridge.douyin_wgc_safe_fallback_patch import install_douyin_wgc_safe_fallback_patch
from bridge.douyin_window_capture_patch import install_douyin_window_capture_patch
from bridge.full_validation import run_full_validation
from bridge.runtime_diagnostics import (
    create_support_bundle,
    current_paths,
    event,
    exception,
    heartbeat_loop,
    install_asyncio_exception_handler,
    mark_graceful_exit,
    redact,
    start_runtime_logging,
)
from bridge.single_instance import try_acquire_bridge_lock

BRIDGE_VERSION = "0.10.3"
BASE_DIR = Path(__file__).resolve().parent
INSTANCE_LOCK_PATH = BASE_DIR / "logs" / "bridge.instance.lock"


def _session_summary(agent_instance: Any) -> dict[str, Any]:
    vtube = getattr(agent_instance, "vtube_studio", None)
    collector = getattr(agent_instance, "douyin_collector", None)
    return {
        "bridge_connected": not agent_instance.stop_event.is_set(),
        "vtube_studio_sessions": vtube.status() if vtube is not None else {},
        "douyin_visible_collector": collector.status() if collector is not None else None,
    }


def install() -> None:
    install_visible_collector_runtime_patch()
    install_douyin_ocr_result_patch()
    install_douyin_window_capture_patch()
    install_douyin_region_occlusion_patch()
    install_douyin_three_channel_patch()
    install_douyin_wgc_hwnd_patch()
    install_douyin_wgc_safe_fallback_patch()
    install_douyin_three_channel_config_patch()
    install_douyin_validation_fix()
    install_douyin_scan_logging_patch()
    install_bridge_control_guard(agent)
    agent.BRIDGE_VERSION = BRIDGE_VERSION
    original_capabilities = agent.BridgeAgent.capabilities
    original_execute = agent.BridgeAgent.execute

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in (
            "provider.tencent_digital_human.reserved",
            "provider.aliyun_avatar.reserved",
            "provider.baidu_xiling.reserved",
            "bridge.single_instance",
            "bridge.control.serialized_send",
            "bridge.diagnostics.paths",
            "bridge.diagnostics.bundle",
            "douyin.visible.region_occlusion_guard",
            "douyin.visible.open_diagnostics_folder",
            "douyin.visible.three_channel",
            "douyin.visible.electron_accessibility",
            "douyin.visible.windows_graphics_capture",
            "douyin.visible.windows_graphics_capture.hwnd",
            "douyin.visible.screen_region_clear_fallback",
            "douyin.visible.channel_probe",
            "aliver.full_validation",
            "provider.avatar.full_validation",
        ):
            if item not in values:
                values.append(item)
        return values

    async def execute(self, command_type, payload):
        started = time.monotonic()
        event("bridge_command_started", command_type=command_type, payload=redact(payload))
        try:
            provider_type = str(payload.get("provider_type") or "")
            if command_type == "provider.start_session" and provider_type in DOMESTIC_PROVIDER_TYPES:
                result = start_domestic_provider(payload)
            elif command_type == "provider.stop_session" and provider_type in DOMESTIC_PROVIDER_TYPES:
                result = stop_domestic_provider(payload)
            elif command_type == "bridge.diagnostics.paths":
                result = current_paths()
            elif command_type == "bridge.diagnostics.bundle":
                result = await asyncio.to_thread(
                    create_support_bundle,
                    reason=str(payload.get("reason") or "控制台手动导出"),
                    minutes=int(payload.get("minutes") or 90),
                )
            elif command_type == "douyin.visible.open_diagnostics_folder":
                result = await asyncio.to_thread(
                    self.douyin_collector.open_diagnostics_folder,
                    str(payload.get("path") or "").strip() or None,
                )
            elif command_type == "douyin.visible.channel_probe":
                result = await asyncio.to_thread(self.douyin_collector.probe_channels)
            elif command_type == "douyin.visible.electron_accessibility.status":
                result = await asyncio.to_thread(
                    self.douyin_collector.electron_accessibility_status,
                    refresh=True,
                )
            elif command_type == "douyin.visible.electron_accessibility.restart":
                result = await asyncio.to_thread(self.douyin_collector.restart_electron_accessibility)
            elif command_type in {"aliver.full_validation", "provider.avatar.full_validation"}:
                result = await run_full_validation(self, dict(payload or {}))
            else:
                result = await original_execute(self, command_type, payload)
            event(
                "bridge_command_completed",
                command_type=command_type,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return result
        except Exception as exc:
            exception(
                "bridge_command_failed",
                exc,
                command_type=command_type,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
            raise

    agent.BridgeAgent.capabilities = staticmethod(capabilities)
    agent.BridgeAgent.execute = execute


async def main() -> None:
    instance_lock, owner = try_acquire_bridge_lock(INSTANCE_LOCK_PATH)
    if instance_lock is None:
        owner_pid = owner.get("pid") or "未知"
        owner_started = owner.get("started_at") or "未知"
        print(
            "检测到另一个 ALiver Bridge 已经在运行，当前进程不会重复启动。"
            f" 已有进程 PID={owner_pid}，启动时间={owner_started}。"
        )
        print("请先关闭旧 Bridge 窗口；找不到窗口时可运行 " r".\scripts\stop_bridge_windows.ps1")
        return

    with instance_lock:
        start_runtime_logging(component="bridge", version=BRIDGE_VERSION)
        install()
        loop = asyncio.get_running_loop()
        install_asyncio_exception_handler(loop)
        instance = agent.BridgeAgent()
        heartbeat = asyncio.create_task(
            heartbeat_loop(lambda: _session_summary(instance), interval_seconds=2.0),
            name="bridge-runtime-heartbeat",
        )
        event("bridge_agent_main_enter", server_url=instance.server_url, instance_owner=owner)
        try:
            await instance.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            exception("bridge_agent_main_failed", exc)
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            collector = getattr(instance, "douyin_collector", None)
            if collector is not None:
                await asyncio.to_thread(collector.stop)
            vtube = getattr(instance, "vtube_studio", None)
            if vtube is not None:
                await vtube.stop_all()
            await asyncio.to_thread(instance.audio.shutdown)
            event("bridge_agent_main_exit")
            mark_graceful_exit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
