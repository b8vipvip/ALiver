import math
from array import array

import pytest

from app.providers.base import ProviderContext
from app.providers.simli import SimliProvider
from bridge.simli_session import SIMLI_SAMPLE_RATE, Pcm16ToSimliConverter


def make_stereo_pcm(sample_rate: int, seconds: float = 1.0) -> bytes:
    values = array("h")
    frames = int(sample_rate * seconds)
    for index in range(frames):
        sample = int(10000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        values.extend((sample, sample))
    return values.tobytes()


def test_pcm_converter_outputs_16khz_mono_pcm16():
    converter = Pcm16ToSimliConverter(48000, 2)
    source = make_stereo_pcm(48000)
    output = converter.convert(source)

    expected_bytes = SIMLI_SAMPLE_RATE * 2
    assert abs(len(output) - expected_bytes) <= 4
    assert len(output) % 2 == 0
    assert max(array("h", output)) > 5000


@pytest.mark.asyncio
async def test_simli_provider_requires_api_key_and_face_id():
    missing_key = SimliProvider(
        ProviderContext(
            provider_id="simli-test",
            name="Simli",
            provider_type="simli",
            api_base_url=None,
            credentials={},
            settings={"face_id": "face-1"},
        )
    )
    result = await missing_key.create_session({})
    assert result.success is False
    assert "api_key" in (result.error or "")

    missing_face = SimliProvider(
        ProviderContext(
            provider_id="simli-test",
            name="Simli",
            provider_type="simli",
            api_base_url=None,
            credentials={"api_key": "secret"},
            settings={},
        )
    )
    result = await missing_face.create_session({})
    assert result.success is False
    assert "face_id" in (result.error or "")


@pytest.mark.asyncio
async def test_simli_provider_builds_bridge_plan():
    provider = SimliProvider(
        ProviderContext(
            provider_id="simli-test",
            name="Simli",
            provider_type="simli",
            api_base_url=None,
            credentials={"api_key": "secret"},
            settings={"face_id": "face-1", "transport": "livekit"},
        )
    )
    result = await provider.create_session({"window_size": [640, 640]})

    assert result.success is True
    assert result.data["execution_mode"] == "bridge"
    assert result.data["provider_type"] == "simli"
    assert result.data["config"]["face_id"] == "face-1"
    assert result.data["config"]["transport"] == "livekit"
    assert result.data["config"]["window_size"] == [640, 640]


def test_simli_provider_api_redacts_key_and_requires_bridge(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "Simli test",
            "provider_type": "simli",
            "api_base_url": "https://api.simli.ai",
            "credentials": {"api_key": "simli-secret"},
            "settings": {"face_id": "face-1", "transport": "livekit"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["provider_type"] == "simli"
    assert body["execution_mode"] == "bridge"
    assert body["credential_keys"] == ["api_key"]
    assert "simli-secret" not in response.text

    session = client.post(
        "/api/sessions",
        json={"provider_config_id": body["id"], "overrides": {}},
    )
    assert session.status_code == 422
    assert "bridge_id" in session.text
