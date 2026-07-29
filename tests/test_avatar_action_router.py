from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.avatar_action_service import director_action_for_command
from app.json_utils import dumps
from app.models import DirectorCommand
from bridge.avatar_action_router import AvatarActionRouter


@dataclass
class FakeClock:
    value: float = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeMotion:
    def __init__(self) -> None:
        self.triggered: list[str] = []
        self.clears = 0
        self.resets = 0

    async def trigger(self, action: str) -> dict:
        self.triggered.append(action)
        return {"action": action}

    async def clear(self) -> None:
        self.clears += 1

    async def reset(self) -> dict:
        self.resets += 1
        return {"reset": True}


def build_router(clock: FakeClock, motion: FakeMotion, cooldown_ms: int = 1200) -> AvatarActionRouter:
    return AvatarActionRouter(
        trigger=motion.trigger,
        clear_transient=motion.clear,
        reset=motion.reset,
        cooldown_ms=cooldown_ms,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_priority_preemption_and_queue_order() -> None:
    clock = FakeClock()
    motion = FakeMotion()
    router = build_router(clock, motion)

    first = await router.submit("thinking", source="director.command", priority=68, duration_ms=5000)
    queued = await router.submit(
        "happy",
        source="live.comment",
        priority=60,
        duration_ms=2000,
        interrupt=False,
    )
    preempt = await router.submit("wave", source="manual.debug", priority=100, duration_ms=2500)

    assert first["accepted"] is True
    assert queued["queued"] is True
    assert preempt["queued"] is False
    status = router.status()
    assert status["active"]["action"] == "wave"
    assert status["active"]["source"] == "manual.debug"
    assert status["queue_count"] == 1
    assert status["queue"][0]["action"] == "happy"
    assert motion.triggered == ["thinking", "wave"]


@pytest.mark.asyncio
async def test_speech_interrupts_director_thinking_and_restores_talking() -> None:
    clock = FakeClock()
    motion = FakeMotion()
    router = build_router(clock, motion)

    await router.submit("thinking", source="director.command", priority=68, duration_ms=8000)
    await router.sync_speech(True)

    status = router.status()
    assert status["active"] is None
    assert status["base_mode"] == "talking"
    assert status["next_state"] == "talking"
    assert any(item["event"] == "finished" and item["reason"] == "speech_started" for item in status["history"])


@pytest.mark.asyncio
async def test_timeout_restores_live_base_mode_and_runs_next_action() -> None:
    clock = FakeClock()
    motion = FakeMotion()
    router = build_router(clock, motion)

    await router.sync_speech(True)
    await router.submit("wave", source="live.follow", priority=82, duration_ms=1000)
    await router.submit(
        "happy",
        source="live.gift",
        priority=70,
        duration_ms=1500,
        interrupt=False,
    )
    clock.advance(1.1)
    await router.sync_speech(True)

    status = router.status()
    assert status["active"]["action"] == "happy"
    assert status["base_mode"] == "talking"
    assert status["queue_count"] == 0

    clock.advance(1.6)
    await router.sync_speech(True)
    status = router.status()
    assert status["active"] is None
    assert status["next_state"] == "talking"


@pytest.mark.asyncio
async def test_cooldown_rejects_duplicate_source_action() -> None:
    clock = FakeClock()
    motion = FakeMotion()
    router = build_router(clock, motion, cooldown_ms=2000)

    await router.submit("happy", source="live.gift", duration_ms=500)
    clock.advance(0.6)
    await router.sync_speech(False)
    result = await router.submit("happy", source="live.gift", duration_ms=500)

    assert result["accepted"] is False
    assert result["reason"] == "cooldown"
    assert result["cooldown_remaining_ms"] > 0


@pytest.mark.asyncio
async def test_reset_clears_queue_and_motion() -> None:
    clock = FakeClock()
    motion = FakeMotion()
    router = build_router(clock, motion)

    await router.submit("wave", source="live.follow", duration_ms=3000)
    await router.submit("happy", source="live.gift", priority=70, interrupt=False)
    result = await router.submit("reset", source="manual.debug", force=True)

    assert result["accepted"] is True
    assert router.status()["queue_count"] == 0
    assert router.status()["active"] is None
    assert motion.resets == 1


def test_director_explicit_action_is_preserved() -> None:
    command = DirectorCommand(
        extension_id="extension-1",
        command_type="director_instruction",
        payload_json=dumps(
            {
                "text": "欢迎新观众",
                "avatar_action": "wave",
                "avatar_action_priority": 92,
                "avatar_action_duration_ms": 2600,
            }
        ),
        status="queued",
        priority=50,
    )

    assert director_action_for_command(command) == ("wave", 92, 2600)
