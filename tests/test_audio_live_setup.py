from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge import audio_live_setup as live


def _scan() -> dict:
    out_capture = {
        "key": "out-loop",
        "name": "CABLE Input (VB-Audio Virtual Cable) [Loopback]",
        "virtual_family": "vb-cable",
    }
    out_playback = {
        "key": "out-play",
        "name": "CABLE Input (VB-Audio Virtual Cable)",
        "virtual_family": "vb-cable",
    }
    out_microphone = {
        "key": "out-mic",
        "name": "CABLE Output (VB-Audio Virtual Cable)",
        "virtual_family": "vb-cable",
    }
    in_playback = {
        "key": "in-play",
        "name": "CABLE-A Input (VB-Audio Cable A)",
        "virtual_family": "vb-cable-a",
    }
    in_microphone = {
        "key": "in-mic",
        "name": "CABLE-A Output (VB-Audio Cable A)",
        "virtual_family": "vb-cable-a",
    }
    return {
        "routes": {
            "ready": True,
            "warnings": [],
            "gpt_out": {"capture": out_capture, "playback": out_playback, "ready": True},
            "gpt_in": {"playback": in_playback, "microphone": in_microphone, "ready": True},
        },
        "virtual_pairs": [
            {
                "family": "vb-cable",
                "loopback": out_capture,
                "playback": out_playback,
                "microphone": out_microphone,
                "complete": True,
            },
            {
                "family": "vb-cable-a",
                "playback": in_playback,
                "microphone": in_microphone,
                "complete": True,
            },
        ],
    }


class FakeAudio:
    def __init__(self) -> None:
        self.active = False
        self.dbfs = -18.0

    def apply_recommendations(self):
        return _scan()["routes"]

    def list_devices(self):
        return _scan()

    def get_routes(self):
        return _scan()["routes"]

    def status(self):
        return {
            "active": self.active,
            "dbfs": self.dbfs if self.active else -96.0,
            "device": {"key": "out-loop"} if self.active else None,
        }

    def start_gpt_out(self, **_kwargs):
        self.active = True
        return self.status()

    def stop(self):
        self.active = False
        return self.status()


class FakeClient:
    def __init__(self) -> None:
        self.injected = []

    async def inject_parameters(self, values):
        self.injected.append(dict(values))
        return {"injected": len(values)}


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-vtube",
        config={"mouth_input_parameter": "VoiceVolume"},
        state={
            "status": "active",
            "started_at": "2026-07-31T00:00:00Z",
            "motion_capabilities": {
                "input_parameters": [
                    {"name": "VoiceVolume"},
                    {"name": "MouthOpen"},
                ]
            },
        },
        client=FakeClient(),
    )


def test_dbfs_mapping_has_gate_and_full_scale():
    assert live.dbfs_to_mouth_value(-60.0) == 0.0
    assert live.dbfs_to_mouth_value(-52.0) == 0.0
    assert 0.0 < live.dbfs_to_mouth_value(-30.0) < 1.0
    assert live.dbfs_to_mouth_value(-10.0) == 1.0


def test_route_targets_use_gpt_out_microphone_for_douyin_and_vtube():
    targets = live._route_targets(_scan())

    assert targets["chrome_output"]["name"].startswith("CABLE Input")
    assert targets["douyin_microphone"]["name"].startswith("CABLE Output")
    assert targets["vtube_microphone"] == targets["douyin_microphone"]
    assert targets["chatgpt_microphone"]["name"].startswith("CABLE-A Output")


def test_fallback_parameters_include_voice_and_standard_mouth():
    assert live._fallback_parameters(_runtime()) == ("VoiceVolume", "MouthOpen")


def test_auto_configure_enables_api_mouth_fallback_when_native_lipsync_is_silent(monkeypatch):
    async def scenario():
        runtime = _runtime()
        audio = FakeAudio()
        agent = SimpleNamespace(
            audio=audio,
            vtube_studio=SimpleNamespace(sessions={runtime.session_id: runtime}),
        )
        manager = live.LiveAudioSetupManager(agent)

        async def silent_native(_agent, _runtime):
            return {"passed": False, "diagnosis": "no movement"}

        monkeypatch.setattr(live, "_mouth_validation", silent_native)
        result = await manager.auto_configure({})
        await asyncio.sleep(0.18)

        assert result["route_ready"] is True
        assert result["mode"] == "api_mouth_fallback"
        assert result["instructions"]["douyin_microphone"].startswith("CABLE Output")
        assert runtime.config["audio_device_name"].startswith("CABLE Output")
        assert runtime.client.injected
        assert any(values.get("MouthOpen", 0.0) > 0 for values in runtime.client.injected)

        stopped = await manager.stop()
        assert stopped["fallback_running"] is False
        assert audio.active is False

    asyncio.run(scenario())


def test_auto_configure_keeps_native_vtube_lipsync_when_validation_passes(monkeypatch):
    async def scenario():
        runtime = _runtime()
        audio = FakeAudio()
        agent = SimpleNamespace(
            audio=audio,
            vtube_studio=SimpleNamespace(sessions={runtime.session_id: runtime}),
        )
        manager = live.LiveAudioSetupManager(agent)

        async def working_native(_agent, _runtime):
            return {"passed": True, "diagnosis": "native ok"}

        monkeypatch.setattr(live, "_mouth_validation", working_native)
        result = await manager.auto_configure({})

        assert result["mode"] == "native_vtube_lipsync"
        assert result["fallback_running"] is False
        assert audio.active is False
        assert runtime.client.injected == []

    asyncio.run(scenario())
