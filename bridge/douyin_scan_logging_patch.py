from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge import douyin_visible_collector as collector

LOG_DIR = Path(__file__).resolve().parent / "logs" / "douyin_visible"
_ORIGINAL_RECORD_SCAN = collector.DouyinVisibleCollectorManager._record_scan


def _record_scan(self, window, lines: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    _ORIGINAL_RECORD_SCAN(self, window, lines, events)
    if not lines and not events:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"collector-{datetime.now().strftime('%Y%m%d')}.jsonl"
    payload = {
        "at": collector.utc_iso(),
        "window": window.as_dict(),
        "capture_source": self._state.get("capture_source"),
        "capture_safety": self._state.get("capture_safety"),
        "ocr_region": dict(self._config.get("ocr_region") or {}),
        "lines": lines,
        "events": events,
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        with self._lock:
            self._state["full_log_path"] = str(path)
    except OSError as exc:
        with self._lock:
            self._state["log_error"] = f"{type(exc).__name__}: {exc}"


def install_douyin_scan_logging_patch() -> None:
    collector.DouyinVisibleCollectorManager._record_scan = _record_scan
