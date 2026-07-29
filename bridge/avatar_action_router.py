from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

ALLOWED_ACTIONS = {"idle", "talking", "thinking", "wave", "happy", "surprised", "reset"}

DEFAULT_DURATIONS_MS = {
    "idle": 0,
    "talking": 2400,
    "thinking": 4500,
    "wave": 2600,
    "happy": 2800,
    "surprised": 1800,
    "reset": 0,
}

SOURCE_PRIORITIES = {
    "manual.debug": 100,
    "manual.director": 95,
    "director.explicit": 90,
    "live.gift": 84,
    "live.follow": 82,
    "live.comment": 78,
    "auto_director": 76,
    "director.command": 68,
    "chatgpt.generating": 64,
    "chatgpt.status": 30,
    "gpt_out.speech": 50,
    "system": 20,
}


@dataclass(slots=True)
class ActionRequest:
    request_id: str
    action: str
    source: str
    priority: int
    duration_ms: int
    interrupt: bool
    force: bool
    created_at: float
    sequence: int
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, int]:
        return (-self.priority, self.sequence)

    def public(self, *, now: float, active: bool = False) -> dict[str, Any]:
        value = {
            "request_id": self.request_id,
            "action": self.action,
            "source": self.source,
            "priority": self.priority,
            "duration_ms": self.duration_ms,
            "interrupt": self.interrupt,
            "force": self.force,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "age_ms": max(0, round((now - self.created_at) * 1000)),
        }
        if active:
            value["active"] = True
        return value


class AvatarActionRouter:
    """Priority/cooldown router for one VTube Studio session.

    The natural-motion engine remains responsible for continuous idle/talking motion.
    This router schedules finite semantic actions on top and restores the live base mode
    after each action finishes.
    """

    def __init__(
        self,
        *,
        trigger: Callable[[str], Awaitable[dict[str, Any]]],
        clear_transient: Callable[[], Awaitable[None]],
        reset: Callable[[], Awaitable[dict[str, Any]]],
        cooldown_ms: int = 1200,
        max_queue: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._trigger = trigger
        self._clear_transient = clear_transient
        self._reset = reset
        self._clock = clock
        self.cooldown_ms = max(0, int(cooldown_ms))
        self.max_queue = max(1, int(max_queue))
        self._sequence = 0
        self._heap: list[tuple[tuple[int, int], ActionRequest]] = []
        self._active: ActionRequest | None = None
        self._active_started = 0.0
        self._active_until = 0.0
        self._speaking = False
        self._base_mode = "idle"
        self._last_triggered: dict[tuple[str, str], float] = {}
        self._history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._last_reason = "initialized"

    @staticmethod
    def default_priority(source: str) -> int:
        normalized = str(source or "system").strip().lower()
        if normalized in SOURCE_PRIORITIES:
            return SOURCE_PRIORITIES[normalized]
        for prefix, priority in SOURCE_PRIORITIES.items():
            if normalized.startswith(f"{prefix}."):
                return priority
        return 60

    def _record(self, event: str, **details: Any) -> None:
        self._history.append({"event": event, "at": time.time(), **details})
        if len(self._history) > 30:
            del self._history[:-30]

    def _cooldown_remaining_ms(self, action: str, source: str, now: float) -> int:
        last = self._last_triggered.get((action, source))
        if last is None:
            return 0
        return max(0, round(self.cooldown_ms - (now - last) * 1000))

    async def submit(
        self,
        action: str,
        *,
        source: str = "system",
        priority: int | None = None,
        duration_ms: int | None = None,
        interrupt: bool = True,
        force: bool = False,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported avatar action: {normalized_action or 'empty'}")
        normalized_source = str(source or "system").strip().lower()[:120] or "system"
        now = self._clock()
        chosen_priority = self.default_priority(normalized_source) if priority is None else int(priority)
        chosen_priority = max(0, min(chosen_priority, 100))
        chosen_duration = (
            DEFAULT_DURATIONS_MS[normalized_action]
            if duration_ms is None
            else max(0, min(int(duration_ms), 120_000))
        )

        async with self._lock:
            if normalized_action == "reset":
                await self._clear_all_locked(reason="reset")
                await self._reset()
                self._last_reason = f"reset by {normalized_source}"
                self._record("reset", source=normalized_source)
                return {"accepted": True, "mode": self._base_mode, "status": self.status(now=now)}

            if normalized_action == "idle":
                await self._clear_all_locked(reason="idle_requested")
                self._last_reason = f"idle by {normalized_source}"
                self._record("base_requested", action="idle", source=normalized_source)
                return {"accepted": True, "mode": "idle", "status": self.status(now=now)}

            remaining = self._cooldown_remaining_ms(normalized_action, normalized_source, now)
            if remaining and not force:
                self._record(
                    "rejected_cooldown",
                    action=normalized_action,
                    source=normalized_source,
                    remaining_ms=remaining,
                )
                return {
                    "accepted": False,
                    "queued": False,
                    "reason": "cooldown",
                    "cooldown_remaining_ms": remaining,
                    "status": self.status(now=now),
                }

            self._sequence += 1
            request = ActionRequest(
                request_id=str(uuid4()),
                action=normalized_action,
                source=normalized_source,
                priority=chosen_priority,
                duration_ms=chosen_duration,
                interrupt=bool(interrupt),
                force=bool(force),
                created_at=now,
                sequence=self._sequence,
                correlation_id=str(correlation_id)[:160] if correlation_id else None,
                metadata=dict(metadata or {}),
            )

            can_preempt = (
                self._active is None
                or request.force
                or (request.interrupt and request.priority >= self._active.priority)
            )
            if can_preempt:
                if self._active is not None:
                    self._record(
                        "preempted",
                        action=self._active.action,
                        source=self._active.source,
                        by_action=request.action,
                        by_source=request.source,
                    )
                await self._activate_locked(request, now)
                return {
                    "accepted": True,
                    "queued": False,
                    "request_id": request.request_id,
                    "status": self.status(now=now),
                }

            if len(self._heap) >= self.max_queue:
                # Drop the oldest/lowest practical item instead of letting the queue grow forever.
                queued = [item[1] for item in self._heap]
                victim = min(queued, key=lambda item: (item.priority, -item.sequence))
                self._heap = [item for item in self._heap if item[1].request_id != victim.request_id]
                heapq.heapify(self._heap)
                self._record("dropped_queue_full", action=victim.action, source=victim.source)

            heapq.heappush(self._heap, (request.sort_key, request))
            self._record(
                "queued",
                request_id=request.request_id,
                action=request.action,
                source=request.source,
                priority=request.priority,
            )
            return {
                "accepted": True,
                "queued": True,
                "request_id": request.request_id,
                "status": self.status(now=now),
            }

    async def sync_speech(self, speaking: bool) -> None:
        now = self._clock()
        async with self._lock:
            changed = bool(speaking) != self._speaking
            self._speaking = bool(speaking)
            self._base_mode = "talking" if self._speaking else "idle"
            if changed:
                self._record("speech_started" if self._speaking else "speech_stopped")

            # A pending/generating thinking pose should yield immediately once speech starts.
            if (
                self._speaking
                and self._active is not None
                and self._active.action == "thinking"
                and self._active.source.startswith(("director", "chatgpt", "auto_director"))
            ):
                await self._finish_active_locked(reason="speech_started")

            await self._tick_locked(now)

    async def clear(
        self,
        *,
        source: str | None = None,
        include_active: bool = True,
        reason: str = "manual_clear",
    ) -> dict[str, Any]:
        async with self._lock:
            if source:
                normalized = str(source).strip().lower()
                self._heap = [item for item in self._heap if item[1].source != normalized]
                heapq.heapify(self._heap)
                if include_active and self._active and self._active.source == normalized:
                    await self._finish_active_locked(reason=reason)
            else:
                self._heap.clear()
                if include_active:
                    await self._finish_active_locked(reason=reason)
            self._record("queue_cleared", source=source, include_active=include_active, reason=reason)
            return self.status()

    async def _activate_locked(self, request: ActionRequest, now: float) -> None:
        if self._active is not None:
            await self._clear_transient()
        self._active = request
        self._active_started = now
        self._active_until = now + request.duration_ms / 1000.0 if request.duration_ms else now
        self._last_triggered[(request.action, request.source)] = now
        self._last_reason = f"{request.action} from {request.source}"
        if request.action == "talking" and self._speaking:
            result: dict[str, Any] = {"base_mode": "talking"}
        else:
            result = await self._trigger(request.action)
        self._record(
            "activated",
            request_id=request.request_id,
            action=request.action,
            source=request.source,
            priority=request.priority,
            duration_ms=request.duration_ms,
            result=result,
        )
        if request.duration_ms <= 0:
            await self._finish_active_locked(reason="zero_duration")

    async def _finish_active_locked(self, *, reason: str) -> None:
        active = self._active
        self._active = None
        self._active_started = 0.0
        self._active_until = 0.0
        if active is not None:
            await self._clear_transient()
            self._record(
                "finished",
                request_id=active.request_id,
                action=active.action,
                source=active.source,
                reason=reason,
                restore=self._base_mode,
            )
        self._last_reason = f"restored {self._base_mode}: {reason}"

    async def _clear_all_locked(self, *, reason: str) -> None:
        self._heap.clear()
        await self._finish_active_locked(reason=reason)

    async def _tick_locked(self, now: float) -> None:
        if self._active is not None and now >= self._active_until:
            await self._finish_active_locked(reason="timeout")
        if self._active is None and self._heap:
            _, request = heapq.heappop(self._heap)
            await self._activate_locked(request, now)

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        current = self._clock() if now is None else now
        active = self._active
        queue = sorted((item[1] for item in self._heap), key=lambda item: item.sort_key)
        remaining_ms = 0
        if active is not None:
            remaining_ms = max(0, round((self._active_until - current) * 1000))
        cooldowns = {
            f"{action}@{source}": max(0, round(self.cooldown_ms - (current - at) * 1000))
            for (action, source), at in self._last_triggered.items()
            if current - at < self.cooldown_ms / 1000.0
        }
        return {
            "base_mode": self._base_mode,
            "speaking": self._speaking,
            "active": active.public(now=current, active=True) if active else None,
            "active_started_monotonic": round(self._active_started, 3) if active else None,
            "remaining_ms": remaining_ms,
            "next_state": active.action if active else self._base_mode,
            "queue_count": len(queue),
            "queue": [item.public(now=current) for item in queue[:12]],
            "cooldown_ms": self.cooldown_ms,
            "cooldowns": cooldowns,
            "last_reason": self._last_reason,
            "history": list(self._history[-12:]),
        }
