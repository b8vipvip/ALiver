from __future__ import annotations

from typing import Any

from bridge import realtime_voice_dsp as dsp
from bridge.livetalking_pcm_client import LiveTalkingPCMClient

LIVETALKING_COMMANDS = (
    "audio.livetalking.configure",
    "audio.livetalking.start",
    "audio.livetalking.stop",
    "audio.livetalking.status",
    "audio.livetalking.interrupt",
)


def _manager(agent_instance: Any) -> Any:
    manager = getattr(agent_instance, "realtime_voice_dsp", None)
    if manager is None:
        manager = dsp.RealtimeVoiceDSPManager(agent_instance)
        agent_instance.realtime_voice_dsp = manager
    return manager


def install_livetalking_dsp_patch() -> None:
    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_livetalking_pcm_patch", False):
        return

    original_init = manager_class.__init__
    original_start = manager_class.start
    original_stop = manager_class.stop
    original_status = manager_class.status
    original_append_recording = manager_class._append_recording
    original_shutdown = manager_class.shutdown

    def init(self: Any, agent: Any) -> None:
        original_init(self, agent)
        self._livetalking_pcm = LiveTalkingPCMClient()

    def start(self: Any, values: dict[str, Any] | None = None) -> dict[str, Any]:
        original_start(self, values)
        client = self._livetalking_pcm
        if client.status().get("config", {}).get("enabled"):
            client.autostart()
        return self.status()

    def append_recording(self: Any, kind: str, samples: Any) -> None:
        original_append_recording(self, kind, samples)
        if kind != "processed":
            return
        sample_rate = int(self._state.get("sample_rate") or self._config.get("sample_rate") or 48000)
        try:
            self._livetalking_pcm.feed(samples, sample_rate)
        except Exception as exc:
            with self._lock:
                self._state["livetalking_feed_error"] = f"{type(exc).__name__}: {exc}"

    def stop(self: Any, *, persist_disable: bool = True) -> dict[str, Any]:
        self._livetalking_pcm.stop(persist_disable=False)
        original_stop(self, persist_disable=persist_disable)
        return self.status()

    def status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        value["livetalking_pcm"] = self._livetalking_pcm.status()
        return value

    def shutdown(self: Any) -> None:
        self._livetalking_pcm.shutdown()
        original_shutdown(self)

    def livetalking_client(self: Any) -> LiveTalkingPCMClient:
        return self._livetalking_pcm

    manager_class.__init__ = init
    manager_class.start = start
    manager_class._append_recording = append_recording
    manager_class.stop = stop
    manager_class.status = status
    manager_class.shutdown = shutdown
    manager_class.livetalking_client = livetalking_client
    manager_class._aliver_livetalking_pcm_patch = True

    # Patch the base BridgeAgent before agent_sync captures its original handlers.
    # agent_sync will therefore retain these capabilities and delegate the five
    # commands below through its existing serialized command wrapper.
    from bridge import agent as agent_module

    agent_class = agent_module.BridgeAgent
    if getattr(agent_class, "_aliver_livetalking_command_patch", False):
        return
    original_capabilities = agent_class.capabilities
    original_execute = agent_class.execute

    def capabilities() -> list[str]:
        values = list(original_capabilities())
        for item in LIVETALKING_COMMANDS:
            if item not in values:
                values.append(item)
        values.extend(
            item
            for item in ("provider.livetalking.pcm", "provider.livetalking.video_only")
            if item not in values
        )
        return values

    async def execute(self: Any, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = _manager(self).livetalking_client()
        if command_type == "audio.livetalking.configure":
            return client.configure(dict(payload or {}))
        if command_type == "audio.livetalking.start":
            if payload:
                client.configure(dict(payload or {}))
            return client.start()
        if command_type == "audio.livetalking.stop":
            return client.stop()
        if command_type == "audio.livetalking.status":
            return client.status()
        if command_type == "audio.livetalking.interrupt":
            return client.interrupt()
        return await original_execute(self, command_type, payload)

    agent_class.capabilities = staticmethod(capabilities)
    agent_class.execute = execute
    agent_class._aliver_livetalking_command_patch = True
