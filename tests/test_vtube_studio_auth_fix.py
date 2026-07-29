import asyncio

import pytest

from bridge import vtube_studio
from bridge.vtube_studio_auth_fix import MIN_AUTHORIZATION_TIMEOUT_SECONDS


def test_vtube_public_config_extends_interactive_authorization_wait():
    config = vtube_studio.public_config(
        {
            "ws_url": "ws://127.0.0.1:8001",
            "authorization_timeout_seconds": 20,
        }
    )

    assert config["authorization_timeout_seconds"] == MIN_AUTHORIZATION_TIMEOUT_SECONDS


def test_vtube_token_timeout_has_actionable_error(monkeypatch):
    client = vtube_studio.VTubeStudioClient({"ws_url": "ws://127.0.0.1:8001"})

    async def always_timeout(*args, **kwargs):
        raise TimeoutError

    # The auth fix wraps the original method in its closure. Reinstall a tiny
    # failing original by replacing the wrapped method's closure target is not
    # practical, so exercise the public behavior through a fake websocket.
    class FakeWebSocket:
        closed = False

        async def send(self, payload):
            return None

        async def recv(self):
            await asyncio.sleep(3600)

    client.ws = FakeWebSocket()
    client.config["authorization_timeout_seconds"] = 0.01

    async def scenario():
        with pytest.raises(RuntimeError, match="等待插件授权超时"):
            await client._request(
                "AuthenticationTokenRequest",
                {"pluginName": "ALiver", "pluginDeveloper": "b8vipvip"},
                timeout=0.01,
            )

    asyncio.run(scenario())
