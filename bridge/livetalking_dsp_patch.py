from __future__ import annotations

from typing import Any

from bridge import realtime_voice_dsp as dsp
from bridge.livetalking_pcm_client import LiveTalkingPCMClient


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
        result = original_start(self, values)
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
