from __future__ import annotations

import asyncio
import csv
import io
import os
import subprocess
from typing import Any

from bridge import agent
from bridge.realtime_voice_dsp import recommend_dsp_routes


def _process_names() -> set[str]:
    if os.name != "nt":
        return set()
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return set()
    names: set[str] = set()
    for row in csv.reader(io.StringIO(result.stdout)):
        if row:
            names.add(str(row[0] or "").strip().lower())
    return names


def _pair(scan: dict[str, Any], family: str) -> dict[str, Any]:
    return next(
        (
            dict(item)
            for item in scan.get("virtual_pairs") or []
            if str(item.get("family") or "") == family
        ),
        {},
    )


def _check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    *,
    automatic: bool,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "automatic": automatic,
    }


class AudioEnvironmentDoctor:
    def __init__(self, bridge_agent: Any) -> None:
        self.agent = bridge_agent

    def _dsp(self):
        manager = getattr(self.agent, "realtime_voice_dsp", None)
        if manager is None:
            from bridge.realtime_voice_dsp import RealtimeVoiceDSPManager

            manager = RealtimeVoiceDSPManager(self.agent)
            self.agent.realtime_voice_dsp = manager
        return manager

    def _scan(self) -> dict[str, Any]:
        manager = self._dsp()
        # The stable DSP engine caches this scan while a stream is active, so
        # the doctor never creates another PortAudio owner during live audio.
        if hasattr(manager, "_scan"):
            scan = manager._scan()
        else:
            scan = self.agent.audio.list_devices()
        if "dsp_recommendation" not in scan:
            scan["dsp_recommendation"] = recommend_dsp_routes(scan)
        return scan

    def check(self) -> dict[str, Any]:
        scan = self._scan()
        raw = _pair(scan, "vb-cable")
        gpt_in = _pair(scan, "vb-cable-a")
        processed = _pair(scan, "vb-cable-b")
        dsp_status = self._dsp().status()
        processes = _process_names()
        checks: list[dict[str, Any]] = []

        for family, label, row in (
            ("vb-cable", "标准 VB-CABLE（ChatGPT 原声）", raw),
            ("vb-cable-a", "CABLE-A（GPT_IN）", gpt_in),
            ("vb-cable-b", "CABLE-B（DSP 直播输出）", processed),
        ):
            complete = bool(row.get("playback") and row.get("microphone"))
            if family == "vb-cable":
                complete = complete and bool(row.get("loopback"))
            checks.append(
                _check(
                    f"pair.{family}",
                    label,
                    "pass" if complete else "fail",
                    "播放端、录音端和所需 Loopback 均已识别。"
                    if complete
                    else "虚拟声卡端点不完整，请在 Windows 声音设备中启用全部端点。",
                    automatic=False,
                )
            )

        rates: list[int] = []
        for row in (raw, gpt_in, processed):
            endpoints = (
                row.get("playback"),
                row.get("microphone"),
                row.get("loopback"),
            )
            for endpoint in endpoints:
                if isinstance(endpoint, dict) and endpoint.get("default_sample_rate"):
                    rates.append(int(endpoint["default_sample_rate"]))
        unique_rates = sorted(set(rates))
        checks.append(
            _check(
                "sample_rates",
                "虚拟声卡采样率",
                "pass" if unique_rates == [48000] else "warn",
                "全部端点为 48000 Hz。"
                if unique_rates == [48000]
                else (
                    f"检测到采样率 {unique_rates or ['未知']}，"
                    "建议所有 CABLE 端点统一为 48000 Hz。"
                ),
                automatic=False,
            )
        )

        recommendation = dict(scan.get("dsp_recommendation") or {})
        expected_input = dict(recommendation.get("input_microphone") or {})
        expected_output = dict(recommendation.get("output_playback") or {})
        configured_input = dict(dsp_status.get("input_device") or {})
        configured_output = dict(dsp_status.get("output_device") or {})
        dsp_route_ok = bool(
            expected_input
            and expected_output
            and str(configured_input.get("virtual_family") or "") == "vb-cable"
            and str(configured_output.get("virtual_family") or "") == "vb-cable-b"
        )
        checks.append(
            _check(
                "aliver.dsp_route",
                "ALiver DSP 三线分配",
                "pass" if dsp_route_ok else "warn",
                "DSP 输入为标准 CABLE，输出为 CABLE-B。"
                if dsp_route_ok
                else "可由 ALiver 自动修正为标准 CABLE → CABLE-B。",
                automatic=True,
            )
        )

        input_dbfs = float(dsp_status.get("input_dbfs") or -96.0)
        output_dbfs = float(dsp_status.get("output_dbfs") or -96.0)
        input_active = input_dbfs > -70.0
        output_active = output_dbfs > -70.0
        checks.append(
            _check(
                "signal.chrome_to_cable",
                "Chrome / ChatGPT → 标准 CABLE",
                "pass" if input_active else "warn",
                f"检测到输入电平 {input_dbfs:.1f} dBFS。"
                if input_active
                else "当前没有输入信号；播放朗读后仍无电平时，请检查 Chrome 输出设备。",
                automatic=False,
            )
        )
        checks.append(
            _check(
                "signal.dsp_output",
                "DSP → CABLE-B 输出",
                "pass" if output_active else "fail" if input_active else "warn",
                f"检测到处理后电平 {output_dbfs:.1f} dBFS。"
                if output_active
                else "输入已有声音，但处理后输出为静音。"
                if input_active
                else "等待输入声音后才能验证处理后输出。",
                automatic=True,
            )
        )

        chrome_running = "chrome.exe" in processes
        douyin_running = any(
            name in processes for name in {"直播伴侣.exe", "webcast_mate.exe"}
        )
        vtube_running = any(
            name in processes for name in {"vtube studio.exe", "vtubestudio.exe"}
        )
        checks.extend(
            [
                _check(
                    "process.chrome",
                    "Google Chrome 进程",
                    "pass" if chrome_running else "warn",
                    "Chrome 正在运行。" if chrome_running else "未检测到 Chrome。",
                    automatic=False,
                ),
                _check(
                    "process.douyin",
                    "抖音直播伴侣进程",
                    "pass" if douyin_running else "warn",
                    "直播伴侣正在运行。" if douyin_running else "未检测到直播伴侣。",
                    automatic=False,
                ),
                _check(
                    "process.vtube",
                    "VTube Studio 进程",
                    "pass" if vtube_running else "warn",
                    "VTube Studio 正在运行。" if vtube_running else "未检测到 VTube Studio。",
                    automatic=False,
                ),
            ]
        )

        vtube_manager = getattr(self.agent, "vtube_studio", None)
        sessions = (
            list(getattr(vtube_manager, "sessions", {}).values())
            if vtube_manager
            else []
        )
        target_name = str(
            dict(recommendation.get("output_microphone") or {}).get("name") or ""
        )
        vtube_targets_ok = all(
            not target_name
            or str(runtime.config.get("audio_device_name") or "") == target_name
            for runtime in sessions
        )
        checks.append(
            _check(
                "vtube.route",
                "VTube Studio 会话麦克风",
                "pass" if vtube_targets_ok else "warn",
                f"活动会话已指向 {target_name}。"
                if vtube_targets_ok and target_name
                else "可自动把 ALiver 管理的 VTube Studio 会话指向 CABLE-B Output。",
                automatic=True,
            )
        )

        failed = [row for row in checks if row["status"] == "fail"]
        warned = [row for row in checks if row["status"] == "warn"]
        return {
            "ok": not failed,
            "status": "failed" if failed else "warning" if warned else "ready",
            "checks": checks,
            "dsp_status": dsp_status,
            "instructions": {
                "chrome_output": dict(raw.get("playback") or {}).get("name"),
                "chatgpt_microphone": dict(gpt_in.get("microphone") or {}).get("name"),
                "douyin_microphone": dict(processed.get("microphone") or {}).get("name"),
                "vtube_microphone": dict(processed.get("microphone") or {}).get("name"),
            },
            "manual_limitations": [
                (
                    "Windows 的每应用输出和直播伴侣内部麦克风选择没有由 ALiver "
                    "使用的稳定公开接口；程序会检测信号并打开设置页，但不会盲目修改"
                    "系统私有配置。"
                ),
                "虚拟声卡高级格式（采样率/位深）只检查，不在直播运行时强制修改。",
            ],
        }

    def apply(self) -> dict[str, Any]:
        manager = self._dsp()
        scan = self._scan()
        recommendation = dict(scan.get("dsp_recommendation") or {})
        if not recommendation.get("ready"):
            warnings = recommendation.get("warnings") or []
            raise RuntimeError(
                warnings[0] if warnings else "三张虚拟声卡尚未完整识别。"
            )

        input_device = dict(recommendation.get("input_microphone") or {})
        output_device = dict(recommendation.get("output_playback") or {})
        output_microphone = dict(recommendation.get("output_microphone") or {})
        running = bool(manager.status().get("running"))
        manager.configure(
            {
                "input_device_key": input_device.get("key"),
                "output_device_key": output_device.get("key"),
                "sample_rate": 48000,
                "channels": 2,
            }
        )

        if not running:
            try:
                self.agent.audio.apply_recommendations()
            except Exception:
                # The DSP route is still valid; surface route-test details in
                # the check result instead of failing the whole repair.
                pass

        vtube_manager = getattr(self.agent, "vtube_studio", None)
        updated_sessions = 0
        if vtube_manager and output_microphone.get("name"):
            for runtime in list(getattr(vtube_manager, "sessions", {}).values()):
                runtime.config["audio_device_name"] = output_microphone["name"]
                updated_sessions += 1

        result = self.check()
        result["applied"] = {
            "aliver_dsp": True,
            "gpt_routes": not running,
            "vtube_sessions": updated_sessions,
            "chrome_system_route": False,
            "douyin_internal_route": False,
        }
        return result

    @staticmethod
    def open_windows_audio_settings() -> dict[str, Any]:
        if os.name != "nt":
            raise RuntimeError("该操作仅支持 Windows。")
        os.startfile("ms-settings:apps-volume")  # type: ignore[attr-defined]
        return {"opened": True, "uri": "ms-settings:apps-volume"}


def _doctor(bridge_agent: Any) -> AudioEnvironmentDoctor:
    value = getattr(bridge_agent, "audio_environment_doctor", None)
    if value is None:
        value = AudioEnvironmentDoctor(bridge_agent)
        bridge_agent.audio_environment_doctor = value
    return value


def install_audio_environment_patch() -> None:
    if getattr(agent.BridgeAgent, "_aliver_audio_environment_patch", False):
        return
    original_execute = agent.BridgeAgent.execute
    original_capabilities = agent.BridgeAgent.capabilities

    async def execute(self, command_type, payload):
        if command_type == "audio.environment.check":
            return await asyncio.to_thread(_doctor(self).check)
        if command_type == "audio.environment.apply":
            return await asyncio.to_thread(_doctor(self).apply)
        if command_type == "audio.environment.open_windows_settings":
            return await asyncio.to_thread(
                _doctor(self).open_windows_audio_settings
            )
        return await original_execute(self, command_type, payload)

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in (
            "audio.environment.check",
            "audio.environment.apply",
            "audio.environment.open_windows_settings",
        ):
            if item not in values:
                values.append(item)
        return values

    agent.BridgeAgent.execute = execute
    agent.BridgeAgent.capabilities = staticmethod(capabilities)
    agent.BridgeAgent._aliver_audio_environment_patch = True
