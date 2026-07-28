from __future__ import annotations

import asyncio

from bridge import agent, simli_session
from bridge.simli_diagnostics import (
    find_runtime,
    install_simli_diagnostics_patch,
    manager_diagnostic_report,
    run_manager_diagnostic,
)
from bridge.simli_sync import SimliSynchronizedRenderer, install_simli_sync_patch
from bridge.simli_sync_compat import install_audio_iterator_compat

BRIDGE_VERSION = "0.5.1"


def _attach_recent_events(manager, session_id, report):
    runtime = find_runtime(manager, session_id)
    renderer = getattr(runtime, "renderer", None)
    report = dict(report)
    report["recent_events"] = list(getattr(renderer, "_diag_events", []))[-30:]
    return report


def install() -> None:
    install_audio_iterator_compat(SimliSynchronizedRenderer)
    install_simli_diagnostics_patch(SimliSynchronizedRenderer)
    install_simli_sync_patch(simli_session)
    agent.BRIDGE_VERSION = BRIDGE_VERSION
    original_capabilities = agent.BridgeAgent.capabilities
    original_execute = agent.BridgeAgent.execute

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in (
            "provider.simli.av_sync",
            "provider.simli.objective_diagnostics",
            "audio.live_out.auto",
        ):
            if item not in values:
                values.append(item)
        return values

    async def execute(self, command_type, payload):
        if command_type == "provider.start_session" and payload.get("provider_type") == "simli":
            payload = dict(payload)
            plan = dict(payload.get("provider_plan") or {})
            config = dict(plan.get("config") or {})
            config["_session_id"] = str(payload.get("session_id") or "unknown-session")
            plan["config"] = config
            payload["provider_plan"] = plan
        if command_type == "provider.simli.status":
            return self.simli.status()
        if command_type == "provider.simli.diagnostics.report":
            session_id = str(payload.get("session_id") or "") or None
            report = manager_diagnostic_report(self.simli, session_id=session_id)
            return _attach_recent_events(self.simli, session_id, report)
        if command_type == "provider.simli.diagnostics.run":
            session_id = str(payload.get("session_id") or "") or None
            report = await run_manager_diagnostic(
                self.simli,
                session_id=session_id,
                duration_seconds=float(payload.get("duration_seconds", 12)),
            )
            return _attach_recent_events(self.simli, session_id, report)
        return await original_execute(self, command_type, payload)

    agent.BridgeAgent.capabilities = staticmethod(capabilities)
    agent.BridgeAgent.execute = execute


async def main() -> None:
    install()
    await agent.main()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
