from __future__ import annotations

from typing import Any

from bridge import realtime_voice_dsp as dsp


def _route_family(route: dict[str, Any], configured: dict[str, Any]) -> str:
    return str(
        configured.get("family")
        or route.get("family")
        or dict(route.get("microphone") or {}).get("virtual_family")
        or dict(route.get("playback") or {}).get("virtual_family")
        or dict(route.get("capture") or {}).get("virtual_family")
        or ""
    )


def _pair_priority(pair: dict[str, Any]) -> tuple[int, str]:
    family = str(pair.get("family") or "")
    priorities = {
        "vb-cable-b": 0,
        "vb-cable-c": 1,
        "vb-cable-d": 2,
        "voicemeeter-aux": 3,
        "voicemeeter-vaio3": 4,
        "voicemeeter-main": 5,
        "vb-cable-a": 8,
        "vb-cable": 9,
    }
    return priorities.get(family, 6), family


def install_realtime_voice_dsp_route_guard() -> None:
    if getattr(dsp, "_aliver_dsp_route_guard_installed", False):
        return

    original_recommend = dsp.recommend_dsp_routes

    def recommend_dsp_routes(scan: dict[str, Any]) -> dict[str, Any]:
        result = dict(original_recommend(scan))
        routes = dict(scan.get("routes") or {})
        configured = dict(routes.get("configured") or {})
        gpt_out = dict(routes.get("gpt_out") or {})
        gpt_in = dict(routes.get("gpt_in") or {})
        configured_out = dict(configured.get("gpt_out") or {})
        configured_in = dict(configured.get("gpt_in") or {})

        raw_family = str(result.get("input_family") or _route_family(gpt_out, configured_out))
        gpt_in_family = _route_family(gpt_in, configured_in)
        forbidden = {family for family in (raw_family, gpt_in_family) if family}

        pairs = [
            dict(item)
            for item in scan.get("virtual_pairs") or []
            if isinstance(item, dict)
            and item.get("playback")
            and item.get("microphone")
            and str(item.get("family") or "") not in forbidden
        ]
        pairs.sort(key=_pair_priority)

        current_family = str(result.get("output_family") or "")
        current_valid = bool(
            current_family
            and current_family not in forbidden
            and result.get("output_playback")
            and result.get("output_microphone")
        )
        selected = None
        if current_valid:
            selected = next(
                (item for item in pairs if str(item.get("family") or "") == current_family),
                None,
            )
        if selected is None and pairs:
            selected = pairs[0]

        warnings = [
            str(item)
            for item in result.get("warnings") or []
            if "没有找到独立的处理后输出虚拟声卡" not in str(item)
        ]
        if selected is None:
            result.update(
                {
                    "output_playback": None,
                    "output_microphone": None,
                    "output_loopback": None,
                    "output_family": None,
                    "ready": False,
                }
            )
            warnings.append(
                "没有可用的独立 DSP 输出虚拟声卡。标准 VB-CABLE 用于 ChatGPT 原声，"
                "CABLE-A 已用于 GPT_IN；请安装并选择 CABLE-B（或另一组独立虚拟声卡）。"
            )
        else:
            family = str(selected.get("family") or "")
            result.update(
                {
                    "output_playback": dict(selected.get("playback") or {}) or None,
                    "output_microphone": dict(selected.get("microphone") or {}) or None,
                    "output_loopback": dict(selected.get("loopback") or {}) or None,
                    "output_family": family or None,
                }
            )
            result["ready"] = bool(
                result.get("input_microphone")
                and result.get("output_playback")
                and result.get("output_microphone")
                and family not in forbidden
            )

        result["input_family"] = raw_family or None
        result["gpt_in_family"] = gpt_in_family or None
        result["forbidden_output_families"] = sorted(forbidden)
        result["warnings"] = warnings

        instructions = dict(result.get("instructions") or {})
        instructions["dsp_output"] = dict(result.get("output_playback") or {}).get("name")
        instructions["douyin_microphone"] = dict(result.get("output_microphone") or {}).get("name")
        instructions["vtube_microphone"] = dict(result.get("output_microphone") or {}).get("name")
        instructions["chatgpt_microphone"] = dict(gpt_in.get("microphone") or {}).get("name")
        result["instructions"] = instructions
        return result

    def resolve(
        self: Any,
        scan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        recommendation = dict(scan.get("dsp_recommendation") or {})
        inputs = [dict(row) for row in scan.get("input_devices") or []]
        outputs = [dict(row) for row in scan.get("output_devices") or []]
        loopbacks = [dict(row) for row in scan.get("loopback_devices") or []]

        input_key = str(self._config.get("input_device_key") or "")
        output_key = str(self._config.get("output_device_key") or "")
        input_device = next((row for row in inputs if row.get("key") == input_key), None)
        input_device = input_device or dict(recommendation.get("input_microphone") or {}) or None
        if input_device is None:
            raise RuntimeError("没有找到 ChatGPT 原声虚拟声卡录音端（通常是 CABLE Output）。")

        input_family = str(input_device.get("virtual_family") or recommendation.get("input_family") or "")
        gpt_in_family = str(recommendation.get("gpt_in_family") or "")
        forbidden = {family for family in (input_family, gpt_in_family) if family}

        output_device = next((row for row in outputs if row.get("key") == output_key), None)
        if output_device and str(output_device.get("virtual_family") or "") in forbidden:
            output_device = None
        output_device = output_device or dict(recommendation.get("output_playback") or {}) or None
        if output_device is None:
            raise RuntimeError(
                "DSP 处理后输出不能复用原声虚拟声卡或 GPT_IN 的 CABLE-A。"
                "请安装并选择 CABLE-B（或另一组独立虚拟声卡）。"
            )

        output_family = str(output_device.get("virtual_family") or "")
        if not output_family or output_family in forbidden:
            raise RuntimeError(
                "DSP 输出虚拟声卡与原声输入/GPT_IN 冲突。"
                "请使用独立的 CABLE-B Output/Input 配对。"
            )

        output_microphone = next(
            (row for row in inputs if row.get("virtual_family") == output_family),
            None,
        )
        if output_microphone is None:
            raise RuntimeError("处理后输出虚拟声卡缺少录音端，直播伴侣无法接收 DSP 声音。")
        output_loopback = next(
            (row for row in loopbacks if row.get("virtual_family") == output_family),
            None,
        )
        return input_device, output_device, output_microphone, output_loopback

    dsp.recommend_dsp_routes = recommend_dsp_routes
    dsp.RealtimeVoiceDSPManager._resolve = resolve
    dsp._aliver_dsp_route_guard_installed = True
