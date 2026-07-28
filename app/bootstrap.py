from __future__ import annotations

from typing import Any

import uvicorn

import app
from app.providers import simli as simli_provider

SERVER_VERSION = "0.7.0"


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def install_provider_patch() -> None:
    if getattr(simli_provider.SimliProvider, "_aliver_sync_patched", False):
        return
    original = simli_provider.SimliProvider._runtime_config

    def runtime_config(self, overrides=None):
        values = dict(self.context.settings)
        values.update(overrides or {})
        config = original(self, overrides)
        config.update(
            {
                "audio_output_device_name": str(
                    values.get("audio_output_device_name")
                    or values.get("live_out_device_name")
                    or ""
                ).strip()
                or None,
                "auto_live_out": bool(values.get("auto_live_out", True)),
                "sync_prebuffer_ms": _clamp_int(
                    values.get("sync_prebuffer_ms"), 80, 2000, 350
                ),
                "video_delay_ms": _clamp_int(values.get("video_delay_ms"), -1000, 2000, 0),
                "late_video_drop_ms": _clamp_int(
                    values.get("late_video_drop_ms"), 50, 1000, 180
                ),
                "sync_debug": bool(values.get("sync_debug", False)),
            }
        )
        return config

    simli_provider.SimliProvider._runtime_config = runtime_config
    simli_provider.SimliProvider._aliver_sync_patched = True


install_provider_patch()
app.__version__ = SERVER_VERSION
from app import main as app_main  # noqa: E402

application = app_main.app
settings = app_main.settings


if __name__ == "__main__":
    uvicorn.run(application, host=settings.host, port=settings.port, reload=False)
