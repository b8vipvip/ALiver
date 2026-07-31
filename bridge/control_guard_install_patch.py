from __future__ import annotations

from typing import Any


def install_control_guard_install_patch() -> None:
    from bridge import control_channel
    from bridge.deferred_collector_startup_patch import install_deferred_collector_startup_patch

    if getattr(control_channel, "_aliver_deferred_install_wrapper", False):
        return
    original = control_channel.install_bridge_control_guard

    def wrapped(agent_module: Any) -> None:
        original(agent_module)
        install_deferred_collector_startup_patch(agent_module)

    control_channel.install_bridge_control_guard = wrapped
    control_channel._aliver_deferred_install_wrapper = True
