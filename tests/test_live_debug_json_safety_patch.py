from __future__ import annotations

import json

from bridge import douyin_visible_collector as collector
from bridge.live_debug_json_safety_patch import json_safe


def test_json_safe_replaces_non_finite_window_scores():
    payload = {
        "status": "running",
        "window_candidates": [
            {"handle": 101, "selection_score": 12234.5},
            {"handle": 102, "selection_score": float("-inf")},
            {"handle": 103, "selection_score": float("inf")},
            {"handle": 104, "selection_score": float("nan")},
        ],
    }

    cleaned = json_safe(payload)

    assert cleaned["window_candidates"][0]["selection_score"] == 12234.5
    assert cleaned["window_candidates"][1]["selection_score"] is None
    assert cleaned["window_candidates"][2]["selection_score"] is None
    assert cleaned["window_candidates"][3]["selection_score"] is None
    json.dumps(cleaned, allow_nan=False)


def test_collector_status_is_wrapped_before_bridge_registration():
    assert getattr(collector.DouyinVisibleCollectorManager, "_aliver_json_safe_status_patch", False) is True
