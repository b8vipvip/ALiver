from __future__ import annotations

import json
from typing import Any

from bridge import douyin_visible_collector as collector

MIGRATION_KEY = "capture_join_notices_v2"


def install_live_debug_defaults() -> None:
    manager_class = collector.DouyinVisibleCollectorManager
    if getattr(manager_class, "_aliver_join_capture_v2", False):
        return

    collector.DEFAULT_CONFIG["capture_join_notices"] = True
    collector.DEFAULT_CONFIG[MIGRATION_KEY] = True
    original_load = manager_class._load_config

    def patched_load_config(self: Any) -> dict[str, Any]:
        value = dict(original_load(self))
        persisted: dict[str, Any] = {}
        try:
            if collector.CONFIG_PATH.exists():
                raw = json.loads(collector.CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    persisted = raw
        except (OSError, ValueError):
            persisted = {}

        # Existing installations inherited the old false default. Migrate them
        # once, then preserve later user choices through the marker.
        if not bool(persisted.get(MIGRATION_KEY)):
            value["capture_join_notices"] = True
            value[MIGRATION_KEY] = True
            try:
                collector.CONFIG_PATH.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return value

    manager_class._load_config = patched_load_config
    manager_class._aliver_join_capture_v2 = True
