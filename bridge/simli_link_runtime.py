from __future__ import annotations

from typing import Any

from bridge.simli_session import utc_iso

VOICE_THRESHOLD_DBFS = -50.0


def install_link_runtime_timestamps(runtime_class: type) -> None:
    if getattr(runtime_class, "_aliver_link_runtime_v1", False):
        return

    original_enqueue = runtime_class._enqueue_audio
    original_sender = runtime_class._sender_loop

    def patched_enqueue(runtime: Any, data: bytes) -> None:
        state = runtime.state
        if data and not state.get("first_audio_chunk_queued_at"):
            state["first_audio_chunk_queued_at"] = utc_iso()
        dbfs = float(state.get("last_input_dbfs") or -96.0)
        if dbfs >= VOICE_THRESHOLD_DBFS and not state.get("first_non_silent_input_at"):
            state["first_non_silent_input_at"] = utc_iso()
            state["first_non_silent_input_dbfs"] = round(dbfs, 2)
        original_enqueue(runtime, data)

    async def patched_sender(runtime: Any) -> None:
        try:
            while not runtime.stop_flag.is_set():
                try:
                    data = await __import__("asyncio").wait_for(runtime.audio_queue.get(), timeout=0.5)
                except TimeoutError:
                    if runtime.renderer and runtime.renderer.stop_event.is_set():
                        runtime.stop_flag.set()
                    continue
                if not runtime.client:
                    continue
                await runtime.client.send(data)
                if not runtime.state.get("first_audio_sent_at"):
                    runtime.state["first_audio_sent_at"] = utc_iso()
                runtime.state["sent_chunks"] += 1
                runtime.state["sent_bytes"] += len(data)
        except __import__("asyncio").CancelledError:
            raise
        except Exception:
            # Preserve the mature failure-classification path in the original implementation.
            # It is safe to delegate only before any successful sends; after a send, re-raise so
            # the runtime guard can capture the exact transport failure rather than double-send.
            if int(runtime.state.get("sent_chunks") or 0) == 0:
                await original_sender(runtime)
                return
            raise

    original_start = runtime_class.start

    async def patched_start(runtime: Any) -> dict[str, Any]:
        runtime.state.setdefault("capture_started_at", None)
        runtime.state.setdefault("first_audio_chunk_queued_at", None)
        runtime.state.setdefault("first_non_silent_input_at", None)
        runtime.state.setdefault("first_non_silent_input_dbfs", None)
        runtime.state.setdefault("first_audio_sent_at", None)
        result = await original_start(runtime)
        if runtime.capture_thread and runtime.capture_thread.is_alive() and not runtime.state.get("capture_started_at"):
            runtime.state["capture_started_at"] = utc_iso()
        return result

    runtime_class._enqueue_audio = patched_enqueue
    runtime_class._sender_loop = patched_sender
    runtime_class.start = patched_start
    runtime_class._aliver_link_runtime_v1 = True
