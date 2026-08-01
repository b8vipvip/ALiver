from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge import realtime_voice_dsp as dsp

CUSTOM_PRESETS_PATH = dsp.BASE_DIR / "realtime_voice_dsp.presets.local.json"
_LIBRARY_LOCK = threading.RLock()
_CUSTOM_IDS: set[str] = set()

SOUND_PARAMETER_KEYS = (
    "pitch_semitones",
    "tone_age",
    "low_cut_hz",
    "bass_db",
    "presence_db",
    "compressor_threshold_db",
    "compressor_ratio",
    "compressor_attack_ms",
    "compressor_release_ms",
    "output_gain_db",
    "limiter_threshold_db",
)

BUILTIN_PRESET_META: dict[str, dict[str, Any]] = {
    "original": {
        "name": "原声整理",
        "description": "不改变音高，只做轻度低切、动态整理和防爆音。",
        "order": 10,
    },
    "natural_girl": {
        "name": "自然少女",
        "description": "轻微提高音高并减薄低频，适合自然聊天。",
        "order": 20,
    },
    "clear_girl": {
        "name": "清透少女",
        "description": "音高变化较小，清晰明亮、咬字靠前。",
        "order": 30,
    },
    "soft_sweet": {
        "name": "软甜少女",
        "description": "比自然少女更甜更轻，但保留可懂度。",
        "order": 40,
    },
    "sweet_young": {
        "name": "甜美小女孩感",
        "description": "更明显的年轻和甜美听感，建议先低音量测试。",
        "order": 50,
    },
    "cute_energy": {
        "name": "俏皮元气",
        "description": "明亮、活泼、动态更集中，适合高互动直播。",
        "order": 60,
    },
    "energetic": {
        "name": "元气少女",
        "description": "较强存在感和压缩，适合热闹场景。",
        "order": 70,
    },
    "gentle": {
        "name": "温柔少女",
        "description": "轻柔、温暖、音高变化较小。",
        "order": 80,
    },
    "soft_whisper": {
        "name": "轻柔耳语",
        "description": "低能量、柔和高频和更稳定的近讲听感。",
        "order": 90,
    },
    "bright_streamer": {
        "name": "明亮主播",
        "description": "音高变化很小，重点增强清晰度和响度稳定。",
        "order": 100,
    },
    "cool_woman": {
        "name": "清冷女声",
        "description": "低频克制、轮廓清楚，甜度较低。",
        "order": 110,
    },
    "warm_sister": {
        "name": "温柔姐姐",
        "description": "偏成熟温暖，保留较自然的基准音高。",
        "order": 120,
    },
    "deep": {
        "name": "沉稳低声线",
        "description": "降低音高并增加厚度，适合沉稳表达。",
        "order": 130,
    },
    "steady_male": {
        "name": "稳重男声",
        "description": "适度降低音高，增强低频和播音稳定度。",
        "order": 140,
    },
}

ADDITIONAL_BUILTIN_PRESETS: dict[str, dict[str, float]] = {
    "clear_girl": {
        "pitch_semitones": 1.0,
        "tone_age": 20.0,
        "low_cut_hz": 82.0,
        "bass_db": -2.0,
        "presence_db": 2.0,
        "compressor_threshold_db": -19.0,
        "compressor_ratio": 1.9,
        "compressor_attack_ms": 7.0,
        "compressor_release_ms": 105.0,
        "output_gain_db": -1.0,
        "limiter_threshold_db": -1.0,
    },
    "soft_sweet": {
        "pitch_semitones": 2.0,
        "tone_age": 40.0,
        "low_cut_hz": 86.0,
        "bass_db": -2.2,
        "presence_db": 1.3,
        "compressor_threshold_db": -20.0,
        "compressor_ratio": 2.1,
        "compressor_attack_ms": 9.0,
        "compressor_release_ms": 125.0,
        "output_gain_db": -1.2,
        "limiter_threshold_db": -1.0,
    },
    "cute_energy": {
        "pitch_semitones": 2.6,
        "tone_age": 50.0,
        "low_cut_hz": 92.0,
        "bass_db": -2.6,
        "presence_db": 2.6,
        "compressor_threshold_db": -22.0,
        "compressor_ratio": 3.0,
        "compressor_attack_ms": 5.0,
        "compressor_release_ms": 90.0,
        "output_gain_db": -1.5,
        "limiter_threshold_db": -1.0,
    },
    "soft_whisper": {
        "pitch_semitones": 0.8,
        "tone_age": 12.0,
        "low_cut_hz": 68.0,
        "bass_db": -0.6,
        "presence_db": -0.2,
        "compressor_threshold_db": -25.0,
        "compressor_ratio": 3.2,
        "compressor_attack_ms": 14.0,
        "compressor_release_ms": 180.0,
        "output_gain_db": -2.0,
        "limiter_threshold_db": -1.5,
    },
    "bright_streamer": {
        "pitch_semitones": 0.7,
        "tone_age": 15.0,
        "low_cut_hz": 72.0,
        "bass_db": -1.0,
        "presence_db": 2.5,
        "compressor_threshold_db": -21.0,
        "compressor_ratio": 2.6,
        "compressor_attack_ms": 6.0,
        "compressor_release_ms": 95.0,
        "output_gain_db": -1.0,
        "limiter_threshold_db": -1.0,
    },
    "cool_woman": {
        "pitch_semitones": 0.4,
        "tone_age": 4.0,
        "low_cut_hz": 82.0,
        "bass_db": -1.3,
        "presence_db": 3.0,
        "compressor_threshold_db": -18.0,
        "compressor_ratio": 1.7,
        "compressor_attack_ms": 10.0,
        "compressor_release_ms": 130.0,
        "output_gain_db": -1.2,
        "limiter_threshold_db": -1.0,
    },
    "warm_sister": {
        "pitch_semitones": 0.2,
        "tone_age": -8.0,
        "low_cut_hz": 60.0,
        "bass_db": 0.8,
        "presence_db": 0.3,
        "compressor_threshold_db": -19.0,
        "compressor_ratio": 1.9,
        "compressor_attack_ms": 12.0,
        "compressor_release_ms": 155.0,
        "output_gain_db": -1.0,
        "limiter_threshold_db": -1.0,
    },
    "steady_male": {
        "pitch_semitones": -1.2,
        "tone_age": -30.0,
        "low_cut_hz": 48.0,
        "bass_db": 1.6,
        "presence_db": 0.7,
        "compressor_threshold_db": -20.0,
        "compressor_ratio": 2.4,
        "compressor_attack_ms": 10.0,
        "compressor_release_ms": 145.0,
        "output_gain_db": -1.2,
        "limiter_threshold_db": -1.0,
    },
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        raise ValueError("请填写声音名称。")
    if len(name) > 40:
        raise ValueError("声音名称不能超过 40 个字符。")
    return name


def _empty_document() -> dict[str, Any]:
    return {"version": 1, "presets": {}}


def _read_document() -> dict[str, Any]:
    if not CUSTOM_PRESETS_PATH.exists():
        return _empty_document()
    try:
        value = json.loads(CUSTOM_PRESETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_document()
    presets = value.get("presets") if isinstance(value, dict) else None
    return {"version": 1, "presets": presets if isinstance(presets, dict) else {}}


def _write_document(document: dict[str, Any]) -> None:
    CUSTOM_PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CUSTOM_PRESETS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(CUSTOM_PRESETS_PATH)


def _sound_values(value: Any) -> dict[str, float]:
    normalized = dsp.normalize_dsp_config(value if isinstance(value, dict) else {})
    return {key: float(normalized[key]) for key in SOUND_PARAMETER_KEYS}


def _sync_custom_registry(document: dict[str, Any] | None = None) -> dict[str, Any]:
    global _CUSTOM_IDS
    document = document or _read_document()
    for preset_id in tuple(_CUSTOM_IDS):
        dsp.DSP_PRESETS.pop(preset_id, None)
    _CUSTOM_IDS = set()
    for preset_id, row in dict(document.get("presets") or {}).items():
        if not str(preset_id).startswith("user-") or not isinstance(row, dict):
            continue
        values = _sound_values(row.get("values") or {})
        dsp.DSP_PRESETS[str(preset_id)] = values
        _CUSTOM_IDS.add(str(preset_id))
    return document


def preset_library() -> dict[str, Any]:
    with _LIBRARY_LOCK:
        document = _sync_custom_registry()
        custom = dict(document.get("presets") or {})
        meta: dict[str, dict[str, Any]] = {}
        for preset_id, values in dsp.DSP_PRESETS.items():
            row = dict(custom.get(preset_id) or {})
            builtin = preset_id not in _CUSTOM_IDS
            builtin_meta = BUILTIN_PRESET_META.get(preset_id, {})
            meta[preset_id] = {
                "id": preset_id,
                "name": row.get("name") or builtin_meta.get("name") or preset_id,
                "description": row.get("description") or builtin_meta.get("description") or "",
                "kind": "builtin" if builtin else "custom",
                "builtin": builtin,
                "order": int(builtin_meta.get("order") or (1000 if builtin else 2000)),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "values": dict(values),
            }
        return {
            "presets": {key: dict(value) for key, value in dsp.DSP_PRESETS.items()},
            "preset_meta": meta,
            "custom_count": len(_CUSTOM_IDS),
            "storage_path": str(CUSTOM_PRESETS_PATH),
        }


def save_custom_preset(
    name: Any,
    values: Any,
    preset_id: str | None = None,
) -> dict[str, Any]:
    clean_name = _clean_name(name)
    with _LIBRARY_LOCK:
        document = _read_document()
        presets = dict(document.get("presets") or {})
        requested_id = str(preset_id or "").strip()
        if requested_id:
            if not requested_id.startswith("user-") or requested_id not in presets:
                raise ValueError("只能覆盖已经保存的自定义声音。")
            target_id = requested_id
            created_at = dict(presets[target_id]).get("created_at") or _utc_iso()
        else:
            target_id = f"user-{uuid.uuid4().hex[:10]}"
            created_at = _utc_iso()
        now = _utc_iso()
        presets[target_id] = {
            "name": clean_name,
            "description": "用户保存的实时 DSP 声音",
            "created_at": created_at,
            "updated_at": now,
            "values": _sound_values(values),
        }
        document = {"version": 1, "presets": presets}
        _write_document(document)
        library = preset_library()
        return {
            **library,
            "saved": dict(library["preset_meta"][target_id]),
        }


def delete_custom_preset(preset_id: Any) -> dict[str, Any]:
    target_id = str(preset_id or "").strip()
    if not target_id.startswith("user-"):
        raise ValueError("内置声音不可删除。")
    with _LIBRARY_LOCK:
        document = _read_document()
        presets = dict(document.get("presets") or {})
        if target_id not in presets:
            raise ValueError("没有找到该自定义声音。")
        deleted = dict(presets.pop(target_id))
        _write_document({"version": 1, "presets": presets})
        library = preset_library()
        return {**library, "deleted": {"id": target_id, **deleted}}


def install_realtime_voice_dsp_preset_library_patch() -> None:
    dsp.DSP_PRESETS.update(ADDITIONAL_BUILTIN_PRESETS)
    _sync_custom_registry()

    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_preset_library_patch", False):
        return

    original_devices = manager_class.devices

    def devices(self: Any) -> dict[str, Any]:
        result = dict(original_devices(self))
        result.update(preset_library())
        return result

    def manager_preset_library(self: Any) -> dict[str, Any]:
        del self
        return preset_library()

    def manager_save_preset(
        self: Any,
        name: Any,
        values: Any,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        del self
        return save_custom_preset(name, values, preset_id)

    def manager_delete_preset(self: Any, preset_id: Any) -> dict[str, Any]:
        result = delete_custom_preset(preset_id)
        with self._lock:
            if str(self._config.get("preset") or "") == str(preset_id):
                self._config = dsp.apply_preset(self._config, "original")
                self._save_config()
                if self._state.get("running"):
                    self._effect_board = self._make_board()
        return result

    manager_class.devices = devices
    manager_class.preset_library = manager_preset_library
    manager_class.save_preset = manager_save_preset
    manager_class.delete_preset = manager_delete_preset
    manager_class._aliver_preset_library_patch = True

    from bridge import agent

    bridge_class = agent.BridgeAgent
    if getattr(bridge_class, "_aliver_preset_library_commands", False):
        return
    original_capabilities = bridge_class.capabilities
    original_execute = bridge_class.execute

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in (
            "audio.dsp.presets",
            "audio.dsp.preset.save",
            "audio.dsp.preset.delete",
        ):
            if item not in values:
                values.append(item)
        return values

    def manager_for(instance: Any) -> Any:
        manager = getattr(instance, "realtime_voice_dsp", None)
        if manager is None:
            manager = dsp.RealtimeVoiceDSPManager(instance)
            instance.realtime_voice_dsp = manager
        return manager

    async def execute(
        self: Any,
        command_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if command_type == "audio.dsp.presets":
            return await asyncio.to_thread(manager_for(self).preset_library)
        if command_type == "audio.dsp.preset.save":
            return await asyncio.to_thread(
                manager_for(self).save_preset,
                payload.get("name"),
                dict(payload.get("values") or {}),
                str(payload.get("preset_id") or "").strip() or None,
            )
        if command_type == "audio.dsp.preset.delete":
            return await asyncio.to_thread(
                manager_for(self).delete_preset,
                payload.get("preset_id"),
            )
        return await original_execute(self, command_type, payload)

    bridge_class.capabilities = staticmethod(capabilities)
    bridge_class.execute = execute
    bridge_class._aliver_preset_library_commands = True
