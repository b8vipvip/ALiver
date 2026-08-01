from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi import FastAPI

from app.windows_asyncio_noise_patch import (
    install_windows_asyncio_noise_filter,
    is_harmless_windows_reset,
)
from bridge.audio_environment_patch import AudioEnvironmentDoctor
from bridge.realtime_voice_dsp_output_guard_patch import _RealtimeBoardAdapter

ROOT = Path(__file__).resolve().parents[1]


def _endpoint(name: str, family: str, kind: str, rate: int = 48000) -> dict:
    return {
        "key": f"{family}-{kind}",
        "name": name,
        "virtual_family": family,
        "default_sample_rate": rate,
        "kind": kind,
    }


def _scan() -> dict:
    raw_playback = _endpoint("CABLE Input", "vb-cable", "output")
    raw_microphone = _endpoint("CABLE Output", "vb-cable", "input")
    raw_loopback = _endpoint("CABLE Input [Loopback]", "vb-cable", "loopback")
    a_playback = _endpoint("CABLE-A Input", "vb-cable-a", "output")
    a_microphone = _endpoint("CABLE-A Output", "vb-cable-a", "input")
    b_playback = _endpoint("CABLE-B Input", "vb-cable-b", "output")
    b_microphone = _endpoint("CABLE-B Output", "vb-cable-b", "input")
    b_loopback = _endpoint("CABLE-B Input [Loopback]", "vb-cable-b", "loopback")
    return {
        "virtual_pairs": [
            {
                "family": "vb-cable",
                "playback": raw_playback,
                "microphone": raw_microphone,
                "loopback": raw_loopback,
            },
            {
                "family": "vb-cable-a",
                "playback": a_playback,
                "microphone": a_microphone,
                "loopback": None,
            },
            {
                "family": "vb-cable-b",
                "playback": b_playback,
                "microphone": b_microphone,
                "loopback": b_loopback,
            },
        ],
        "routes": {
            "gpt_out": {
                "family": "vb-cable",
                "capture": raw_loopback,
                "playback": raw_playback,
            },
            "gpt_in": {
                "family": "vb-cable-a",
                "playback": a_playback,
                "microphone": a_microphone,
            },
        },
        "input_devices": [raw_microphone, a_microphone, b_microphone],
        "output_devices": [raw_playback, a_playback, b_playback],
        "loopback_devices": [raw_loopback, b_loopback],
    }


class _FakeDSP:
    def __init__(self, input_dbfs: float = -16.0, output_dbfs: float = -96.0) -> None:
        self.scan = _scan()
        self.configured = None
        self.value = {
            "running": True,
            "input_dbfs": input_dbfs,
            "output_dbfs": output_dbfs,
            "input_device": self.scan["virtual_pairs"][0]["microphone"],
            "output_device": self.scan["virtual_pairs"][2]["playback"],
        }

    def _scan(self):
        return self.scan

    def status(self):
        return dict(self.value)

    def configure(self, values):
        self.configured = dict(values)
        return self.status()


class _FakeAudio:
    def apply_recommendations(self):
        return {"ok": True}


class _Runtime:
    def __init__(self) -> None:
        self.config = {}


class _FakeAgent:
    def __init__(self, dsp: _FakeDSP | None = None) -> None:
        self.realtime_voice_dsp = dsp or _FakeDSP()
        self.audio = _FakeAudio()
        self.vtube_studio = SimpleNamespace(sessions={"one": _Runtime()})


def test_pitch_adapter_uses_overlap_add_instead_of_resetting_every_block() -> None:
    class Board:
        def __init__(self) -> None:
            self.resets: list[bool] = []

        def __call__(self, audio, sample_rate, *, reset=False):
            del sample_rate
            self.resets.append(bool(reset))
            if reset:
                return np.asarray(audio, dtype=np.float32)
            return np.empty((1, 0), dtype=np.float32)

    manager = SimpleNamespace(
        _lock=threading.RLock(),
        _state={"io_latency_ms": 42.67},
    )
    board = Board()
    adapter = _RealtimeBoardAdapter(manager, board, pitch_enabled=True)
    audio = np.ones((2, 1024), dtype=np.float32) * 0.2

    outputs = [adapter(audio, 48000, reset=False) for _ in range(8)]

    assert all(output.shape == audio.shape for output in outputs)
    assert max(float(np.max(np.abs(output))) for output in outputs) > 0.1
    assert 1 <= len(board.resets) < len(outputs)
    assert all(board.resets)
    assert manager._state["processing_mode"] == "pedalboard-overlap-add"
    assert manager._state["pitch_window_frames"] == 4096
    assert manager._state["estimated_latency_ms"] > 100


def test_audio_environment_detects_input_without_dsp_output() -> None:
    result = AudioEnvironmentDoctor(_FakeAgent()).check()
    rows = {row["id"]: row for row in result["checks"]}

    assert rows["pair.vb-cable"]["status"] == "pass"
    assert rows["pair.vb-cable-a"]["status"] == "pass"
    assert rows["pair.vb-cable-b"]["status"] == "pass"
    assert rows["signal.chrome_to_cable"]["status"] == "pass"
    assert rows["signal.dsp_output"]["status"] == "fail"
    assert result["instructions"]["douyin_microphone"] == "CABLE-B Output"


def test_audio_environment_treats_idle_signal_as_information_not_warning() -> None:
    doctor = AudioEnvironmentDoctor(_FakeAgent(_FakeDSP(-96.0, -96.0)))
    result = doctor.check()
    rows = {row["id"]: row for row in result["checks"]}

    assert rows["signal.chrome_to_cable"]["status"] == "info"
    assert rows["signal.dsp_output"]["status"] == "info"
    assert result["signal_validation_required"] is True


def test_audio_environment_live_signal_verification_passes() -> None:
    doctor = AudioEnvironmentDoctor(_FakeAgent(_FakeDSP(-18.0, -20.0)))

    result = doctor.verify_signal(1.0)

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["input_peak_dbfs"] == -18.0
    assert result["output_peak_dbfs"] == -20.0


def test_audio_environment_apply_updates_aliver_and_vtube_routes() -> None:
    fake_agent = _FakeAgent()
    result = AudioEnvironmentDoctor(fake_agent).apply()

    assert fake_agent.realtime_voice_dsp.configured["input_device_key"] == (
        "vb-cable-input"
    )
    assert fake_agent.realtime_voice_dsp.configured["output_device_key"] == (
        "vb-cable-b-output"
    )
    assert fake_agent.vtube_studio.sessions["one"].config[
        "audio_device_name"
    ] == "CABLE-B Output"
    assert result["applied"]["vtube_sessions"] == 1
    assert result["applied"]["chrome_system_route"] is False


def test_ui_exposes_environment_doctor_and_live_signal_check() -> None:
    script = (ROOT / "app/static/realtime_voice_dsp_ui_patch.js").read_text(
        encoding="utf-8"
    )

    assert "一键检查并修复" in script
    assert "播放中验证信号" in script
    assert "audio.environment.check" in script
    assert "audio.environment.apply" in script
    assert "audio.environment.verify_signal" in script
    assert "audio.environment.open_windows_settings" in script
    assert "input_without_output" in script
    assert "处理后输出为静音" in script


def test_windows_reset_noise_filter_is_narrow() -> None:
    class WindowsReset(ConnectionResetError):
        winerror = 10054

    harmless = {
        "message": (
            "Exception in callback "
            "_ProactorBasePipeTransport._call_connection_lost(None)"
        ),
        "exception": WindowsReset("reset"),
    }
    unrelated = {
        "message": "application task failed",
        "exception": RuntimeError("boom"),
    }

    assert is_harmless_windows_reset(harmless) is True
    assert is_harmless_windows_reset(unrelated) is False


def test_windows_reset_filter_wraps_the_real_fastapi_lifespan_loop() -> None:
    lifecycle: list[str] = []

    @asynccontextmanager
    async def base_lifespan(_application):
        lifecycle.append("started")
        yield
        lifecycle.append("stopped")

    application = FastAPI(lifespan=base_lifespan)
    install_windows_asyncio_noise_filter(application)

    class WindowsReset(ConnectionResetError):
        winerror = 10054

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        forwarded: list[dict] = []

        def previous(_loop, context):
            forwarded.append(context)

        loop.set_exception_handler(previous)
        try:
            async with application.router.lifespan_context(application):
                current = loop.get_exception_handler()
                assert current is not None
                assert current is not previous
                assert application.state.windows_asyncio_noise_filter_active is True

                current(
                    loop,
                    {
                        "message": (
                            "Exception in callback "
                            "_ProactorBasePipeTransport._call_connection_lost(None)"
                        ),
                        "exception": WindowsReset("reset"),
                    },
                )
                current(
                    loop,
                    {
                        "message": "application task failed",
                        "exception": RuntimeError("boom"),
                    },
                )
                assert len(forwarded) == 1

            assert loop.get_exception_handler() is previous
            assert application.state.windows_asyncio_noise_filter_active is False
        finally:
            loop.set_exception_handler(None)

    asyncio.run(exercise())

    assert lifecycle == ["started", "stopped"]
