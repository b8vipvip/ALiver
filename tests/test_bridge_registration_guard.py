from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from bridge.fast_startup_patch import _hard_timeout, _registration_metadata
from bridge import heartbeat_metadata_guard_patch as metadata_patch


def test_registration_metadata_never_calls_full_system_info() -> None:
    class FakeBridge:
        _aliver_static_system_info = {
            "platform": "Windows-10.0.19045",
            "python": "3.12.0",
            "hostname": "test-host",
            "pid": 123,
        }
        _aliver_core_init_ms = 12.5

        def system_info(self):
            raise AssertionError("registration must not build full metadata")

    result = _registration_metadata(FakeBridge(), "0.12.0")

    assert result["platform"] == "Windows-10.0.19045"
    assert result["bridge_version"] == "0.12.0"
    assert result["core_init_ms"] == 12.5
    assert result["metadata_phase"] == "registration_minimal"


def test_registration_hard_timeout_is_enforced() -> None:
    async def slow():
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        asyncio.run(_hard_timeout(slow(), seconds=0.01))


def test_heartbeat_uses_cache_when_full_metadata_blocks(monkeypatch) -> None:
    gate = threading.Event()

    class FakeBridge:
        def __init__(self) -> None:
            self._control_metadata_cache = {"cached": True}
            self._aliver_static_system_info = {
                "platform": "Windows",
                "python": "3.12",
                "hostname": "host",
                "pid": 456,
            }

        def system_info(self):
            gate.wait(timeout=1)
            return {"full": True}

    fake_agent = SimpleNamespace(BridgeAgent=FakeBridge)
    monkeypatch.setattr(metadata_patch, "METADATA_WAIT_SECONDS", 0.01)
    metadata_patch.install_heartbeat_metadata_guard_patch(fake_agent)
    instance = FakeBridge()

    async def exercise():
        started = time.monotonic()
        result = await instance._aliver_metadata_for_heartbeat()
        elapsed = time.monotonic() - started
        gate.set()
        await asyncio.sleep(0.02)
        return result, elapsed

    result, elapsed = asyncio.run(exercise())

    assert elapsed < 0.2
    assert result["cached"] is True
    assert result["metadata_phase"] == "heartbeat_cached"
    assert result["metadata_refresh_pending"] is True


def test_control_guard_installs_deferred_metadata_patch() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "bridge/control_guard_install_patch.py").read_text(encoding="utf-8")
    assert "install_heartbeat_metadata_guard_patch" in source
    assert "install_deferred_collector_startup_patch" in source
