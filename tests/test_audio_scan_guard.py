from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from bridge.audio_scan_guard_patch import _guarded_scan


def test_guarded_scan_serializes_and_collapses_startup_burst():
    manager = SimpleNamespace()
    counters = {"calls": 0, "active": 0, "max_active": 0}
    counter_lock = threading.Lock()
    start = threading.Barrier(4)

    def original():
        with counter_lock:
            counters["calls"] += 1
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
        time.sleep(0.05)
        with counter_lock:
            counters["active"] -= 1
        return {"devices": [{"name": "CABLE Output"}]}

    def run_scan():
        start.wait()
        return _guarded_scan(manager, original)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_scan) for _ in range(3)]
        start.wait()
        results = [future.result(timeout=2) for future in futures]

    assert counters["calls"] == 1
    assert counters["max_active"] == 1
    assert all(result == results[0] for result in results)

    results[0]["devices"][0]["name"] = "mutated"
    assert results[1]["devices"][0]["name"] == "CABLE Output"
