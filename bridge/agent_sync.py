from __future__ import annotations

import asyncio
import os
import time
from typing import Any

# Disable OpenCV acceleration paths that have caused native crashes on some Windows drivers.
os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from bridge import agent, simli_session
from bridge.runtime_diagnostics import (
    create_support_bundle,
    current_paths,
    event,
    exception,
    heartbeat_loop,
    install_asyncio_exception_handler,
    mark_graceful_exit,
    redact,
    start_runtime_logging,
)
from bridge.simli_crash_guard import install_simli_crash_guard, install_simli_runtime_guard
from bridge.simli_diagnostics import (
    find_runtime,
    install_simli_diagnostics_patch,
    manager_diagnostic_report,
    run_manager_diagnostic,
)
from bridge.simli_sync import SimliSynchronizedRenderer, install_simli_sync_patch
from bridge.simli_sync_compat import install_audio_iterator_compat

BRIDGE_VERSION = "0.5.2"


def _attach_recent_events(manager, session_id, report):
    runtime = find_runtime(manager, session_id)
    renderer = getattr(runtime, "renderer", None)
    report = dict(report)
    report["recent_events"] = list(getattr(renderer, "_diag_events", []))[-30:]
    report["bridge_runtime_logs"] = current_paths()
    return report


def _session_summary(agent_instance: Any) -> dict[str, Any]:
    sessions = getattr(getattr(agent_instance, "simli", None), "sessions", {})
    return {
        "bridge_connected": not agent_instance.stop_event.is_set(),
        "simli_sessions": {
            session_id: {
                "status": runtime.state.get("status"),
                "phase": runtime.state.get("phase"),
                "phase_zh": runtime.state.get("phase_zh"),
                "sent_chunks": runtime.state.get("sent_chunks"),
                "last_input_dbfs": runtime.state.get("last_input_dbfs"),
                "renderer_task_done": bool(runtime.renderer_task and runtime.renderer_task.done()),
                "sender_task_done": bool(runtime.sender_task and runtime.sender_task.done()),
            }
            for session_id, runtime in sessions.items()
        },
    }


def install() -> None:
    install_audio_iterator_compat(SimliSynchronizedRenderer)
    install_simli_diagnostics_patch(SimliSynchronizedRenderer)
    # Install last so it replaces the risky per-frame OpenCV diagnostic path.
    install_simli_crash_guard(SimliSynchronizedRenderer)
    install_simli_runtime_guard(simli_session.SimliRuntime)
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
            "bridge.diagnostics.paths",
            "bridge.diagnostics.bundle",
        ):
            if item not in values:
                values.append(item)
        return values

    async def execute(self, command_type, payload):
        started = time.monotonic()
        event("bridge_command_started", command_type=command_type, payload=redact(payload))
        try:
            if command_type == "provider.start_session" and payload.get("provider_type") == "simli":
                payload = dict(payload)
                plan = dict(payload.get("provider_plan") or {})
                config = dict(plan.get("config") or {})
                config["_session_id"] = str(payload.get("session_id") or "unknown-session")
                plan["config"] = config
                payload["provider_plan"] = plan
                event(
                    "simli_session_start_requested",
                    session_id=config["_session_id"],
                    transport=config.get("transport"),
                    model=config.get("model"),
                    face_id_present=bool(config.get("face_id")),
                    play_return_audio=config.get("play_return_audio"),
                )
            if command_type == "provider.simli.status":
                result = self.simli.status()
            elif command_type == "provider.simli.diagnostics.report":
                session_id = str(payload.get("session_id") or "") or None
                report = manager_diagnostic_report(self.simli, session_id=session_id)
                result = _attach_recent_events(self.simli, session_id, report)
            elif command_type == "provider.simli.diagnostics.run":
                session_id = str(payload.get("session_id") or "") or None
                report = await run_manager_diagnostic(
                    self.simli,
                    session_id=session_id,
                    duration_seconds=float(payload.get("duration_seconds", 12)),
                )
                result = _attach_recent_events(self.simli, session_id, report)
            elif command_type == "bridge.diagnostics.paths":
                result = current_paths()
            elif command_type == "bridge.diagnostics.bundle":
                result = await asyncio.to_thread(
                    create_support_bundle,
                    reason=str(payload.get("reason") or "控制台手动导出"),
                    minutes=int(payload.get("minutes") or 90),
                )
            else:
                result = await original_execute(self, command_type, payload)
            event(
                "bridge_command_completed",
                command_type=command_type,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return result
        except Exception as exc:
            exception(
                "bridge_command_failed",
                exc,
                command_type=command_type,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
            raise

    agent.BridgeAgent.capabilities = staticmethod(capabilities)
    agent.BridgeAgent.execute = execute


async def main() -> None:
    start_runtime_logging(component="bridge", version=BRIDGE_VERSION)
    install()
    loop = asyncio.get_running_loop()
    install_asyncio_exception_handler(loop)
    instance = agent.BridgeAgent()
    heartbeat = asyncio.create_task(
        heartbeat_loop(lambda: _session_summary(instance), interval_seconds=2.0),
        name="bridge-runtime-heartbeat",
    )
    event("bridge_agent_main_enter", server_url=instance.server_url)
    try:
        await instance.run()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        exception("bridge_agent_main_failed", exc)
        raise
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await instance.simli.stop_all()
        finally:
            await asyncio.to_thread(instance.audio.shutdown)
        event("bridge_agent_main_exit")
        mark_graceful_exit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
