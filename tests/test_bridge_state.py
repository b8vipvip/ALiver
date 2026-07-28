import pytest

from app.bridge_hub import BridgeHub
from app.session_reconciliation import classify_local_session


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_stale_disconnect_does_not_remove_replacement_connection():
    hub = BridgeHub()
    first = FakeWebSocket()
    second = FakeWebSocket()

    first_connection = await hub.connect("bridge-1", first)
    second_connection = await hub.connect("bridge-1", second)

    assert first.closed is True
    assert first.close_code == 1012
    assert hub.connection_id("bridge-1") == second_connection

    removed_stale = await hub.disconnect("bridge-1", first_connection)

    assert removed_stale is False
    assert hub.is_connected("bridge-1") is True
    assert hub.connection_id("bridge-1") == second_connection

    removed_current = await hub.disconnect("bridge-1", second_connection)

    assert removed_current is True
    assert hub.is_connected("bridge-1") is False


def test_missing_local_session_is_classified_as_interrupted():
    code, message = classify_local_session(None)

    assert code == "bridge_session_missing"
    assert "本地不存在" in message


def test_zombie_local_tasks_are_classified_as_interrupted():
    code, message = classify_local_session(
        {
            "status": "active",
            "renderer_task_done": True,
            "sender_task_done": True,
        }
    )

    assert code == "bridge_session_tasks_stopped"
    assert "已经停止" in message


def test_healthy_active_local_session_is_not_interrupted():
    finding = classify_local_session(
        {
            "status": "active",
            "renderer_task_done": False,
            "sender_task_done": False,
        }
    )

    assert finding is None
