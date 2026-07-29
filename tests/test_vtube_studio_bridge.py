import asyncio

from bridge import vtube_studio


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.closed = False
        self.force_authorize = False
        self.triggered = []

    async def connect(self, *, force_authorize=False):
        self.force_authorize = force_authorize
        return self._snapshot()

    async def snapshot(self):
        return self._snapshot()

    async def trigger_hotkey(self, identifier):
        self.triggered.append(identifier)
        return {
            "triggered": True,
            "hotkey_id": "hotkey-1",
            "hotkey_name": identifier,
            "hotkey_type": "TriggerAnimation",
        }

    def clear_token(self):
        return None

    async def close(self):
        self.closed = True

    @staticmethod
    def _snapshot():
        return {
            "connected": True,
            "authenticated": True,
            "api": {
                "version": "1.30.0",
                "url": "ws://127.0.0.1:8001",
                "framerate": 60,
                "connected_plugins": 1,
            },
            "model": {
                "loaded": True,
                "name": "Test Model",
                "id": "model-1",
                "json": "test.vtube.json",
            },
            "hotkeys": [
                {
                    "name": "Wave",
                    "type": "TriggerAnimation",
                    "hotkeyID": "hotkey-1",
                    "file": "wave.motion3.json",
                }
            ],
        }


def payload():
    return {
        "session_id": "session-vts",
        "provider_id": "provider-vts",
        "provider_name": "Local VTube Studio",
        "provider_type": "vtube_studio",
        "provider_plan": {
            "config": {
                "ws_url": "ws://127.0.0.1:8001",
                "require_model_loaded": True,
                "action_cooldown_ms": 0,
                "hotkeys": {"wave": "Wave"},
            }
        },
    }


def test_vtube_manager_starts_loads_model_triggers_action_and_stops(monkeypatch):
    monkeypatch.setattr(vtube_studio, "VTubeStudioClient", FakeClient)

    async def scenario():
        manager = vtube_studio.VTubeStudioSessionManager()
        started = await manager.start(payload())

        assert started["status"] == "active"
        assert started["model"]["name"] == "Test Model"
        assert started["config"]["hotkeys"]["wave"] == "Wave"

        status = manager.status("session-vts")
        assert status["api"]["framerate"] == 60

        action = await manager.action("session-vts", action="wave")
        assert action["action_result"]["hotkey_name"] == "Wave"
        assert action["last_action"] == "wave"

        authorized = await manager.authorize("session-vts")
        assert authorized["authenticated"] is True

        stopped = await manager.stop("session-vts")
        assert stopped["status"] == "ended"
        assert manager.status("session-vts")["status"] == "missing"

    asyncio.run(scenario())


def test_vtube_manager_rejects_unloaded_model(monkeypatch):
    class NoModelClient(FakeClient):
        @staticmethod
        def _snapshot():
            value = FakeClient._snapshot()
            value["model"] = {"loaded": False, "name": None, "id": None, "json": None}
            return value

    monkeypatch.setattr(vtube_studio, "VTubeStudioClient", NoModelClient)

    async def scenario():
        manager = vtube_studio.VTubeStudioSessionManager()
        try:
            await manager.start(payload())
        except RuntimeError as exc:
            assert "no Live2D model" in str(exc)
        else:
            raise AssertionError("Expected missing model failure")

        assert manager.status("session-vts")["status"] == "failed"

    asyncio.run(scenario())
