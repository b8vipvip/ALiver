from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LIVETALKING = ROOT / "services" / "livetalking"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_vendored_source_is_traceable_and_licensed() -> None:
    metadata = json.loads(
        (LIVETALKING / "UPSTREAM.json").read_text(encoding="utf-8")
    )
    assert metadata["upstream"] == "https://github.com/lipku/LiveTalking"
    assert len(metadata["commit"]) == 40
    assert len(metadata["archive_sha256"]) == 64
    assert metadata["copied_files"] >= 100
    assert metadata["license"] == "Apache-2.0"
    assert (LIVETALKING / "UPSTREAM_LICENSE").exists()
    assert not (LIVETALKING / "models" / "wav2lip.pth").exists()


def test_pcm_protocol_is_strict_20ms_s16le() -> None:
    protocol = load_module(
        "livetalking_protocol_test",
        LIVETALKING / "aliver_integration" / "protocol.py",
    )
    assert protocol.SAMPLE_RATE == 16_000
    assert protocol.CHANNELS == 1
    assert protocol.FRAME_MS == 20
    assert protocol.SAMPLES_PER_FRAME == 320
    assert protocol.BYTES_PER_FRAME == 640
    value = protocol.StartMessage.from_payload(
        {
            "type": "start",
            "session_id": "123456",
            "stream_id": "test-stream",
            "format": "s16le",
            "sample_rate": 16_000,
            "channels": 1,
            "frame_ms": 20,
        }
    )
    assert value.public_dict()["bytes_per_frame"] == 640


def test_streaming_resampler_keeps_state_across_blocks() -> None:
    module = load_module(
        "livetalking_client_test",
        ROOT / "bridge" / "livetalking_pcm_client.py",
    )
    angles = np.arange(4800, dtype=np.float32) * (2 * np.pi * 440 / 48000)
    source = np.sin(angles)
    resampler = module.StreamingLinearResampler()
    chunks = [
        resampler.process(source[index : index + 480], 48_000)
        for index in range(0, source.size, 480)
    ]
    rendered = np.concatenate(chunks)
    assert 1590 <= rendered.size <= 1601
    assert np.max(np.abs(rendered)) <= 1.01
    assert np.isfinite(rendered).all()


def test_client_status_never_returns_secret_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_module(
        "livetalking_client_secret_test",
        ROOT / "bridge" / "livetalking_pcm_client.py",
    )
    monkeypatch.setattr(module, "CONFIG_PATH", tmp_path / "livetalking.json")
    client = module.LiveTalkingPCMClient()
    status = client.configure(
        {
            "ws_url": "wss://gpu.example.com/api/aliver/pcm",
            "token": "top-secret-token",
            "session_id": "123",
        }
    )
    assert "token" not in status["config"]
    assert status["config"]["token_configured"] is True
    assert "top-secret-token" not in json.dumps(status)


def test_cloud_runtime_is_video_only_and_registers_pcm_routes() -> None:
    routes = (LIVETALKING / "server" / "routes.py").read_text(
        encoding="utf-8"
    )
    rtc = (LIVETALKING / "server" / "rtc_manager.py").read_text(
        encoding="utf-8"
    )
    webrtc = (LIVETALKING / "server" / "webrtc.py").read_text(
        encoding="utf-8"
    )
    pcm = (LIVETALKING / "aliver_integration" / "routes.py").read_text(
        encoding="utf-8"
    )
    assert "setup_aliver_routes(app)" in routes
    assert '"/api/aliver/pcm"' in pcm
    assert '"/api/aliver/health"' in pcm
    assert "ALIVER_STREAM_TOKEN" in pcm
    assert "ALIVER_PCM_MAX_QUEUE_MS" in pcm
    assert "audio_enabled=not video_only" in rtc
    assert "if not video_only:" in rtc
    assert "if self.__audio is None:" in webrtc
    assert "def clear_queues" in webrtc


def test_bridge_commands_and_dsp_split_are_installed() -> None:
    patch = (ROOT / "bridge" / "livetalking_dsp_patch.py").read_text(
        encoding="utf-8"
    )
    bridge_init = (ROOT / "bridge" / "__init__.py").read_text(
        encoding="utf-8"
    )
    for command in (
        "audio.livetalking.configure",
        "audio.livetalking.start",
        "audio.livetalking.stop",
        "audio.livetalking.status",
        "audio.livetalking.interrupt",
    ):
        assert command in patch
    assert 'kind != "processed"' in patch
    assert "install_livetalking_dsp_patch()" in bridge_init
