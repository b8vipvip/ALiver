from __future__ import annotations

from typing import Any

import numpy as np

from bridge import realtime_voice_dsp as dsp


class _RealtimeBoardAdapter:
    """Make Pedalboard's buffered PitchShift usable in a fixed-block loop.

    Pedalboard may return a zero-length array when PitchShift is called on a
    short block with reset=False. The previous engine padded that empty result
    with zeros, so CABLE-B stayed silent although the DSP input meter moved.

    Until ALiver ships a dedicated streaming formant/pitch engine, pitch-enabled
    profiles are processed as independent fixed blocks with reset=True. This is
    deterministic, always returns audio, and keeps the realtime route alive.
    Profiles without pitch keep the stateful reset=False path.
    """

    def __init__(self, manager: Any, board: Any, *, block_reset: bool) -> None:
        self.manager = manager
        self.board = board
        self.block_reset = bool(block_reset)

    def __call__(self, audio: np.ndarray, sample_rate: float, *, reset: bool = False):
        force_reset = self.block_reset or bool(reset)
        result = self.board(audio, sample_rate, reset=force_reset)
        value = np.asarray(result)

        # Defensive fallback for plugins that unexpectedly buffer the entire
        # input block. Never convert an active input block into silent padding.
        if value.size == 0 and np.asarray(audio).size:
            result = self.board(audio, sample_rate, reset=True)
            value = np.asarray(result)
            with self.manager._lock:
                self.manager._state["empty_output_recoveries"] = int(
                    self.manager._state.get("empty_output_recoveries") or 0
                ) + 1
                self.manager._state["processing_mode"] = "pedalboard-block-reset-recovery"
        else:
            with self.manager._lock:
                self.manager._state["processing_mode"] = (
                    "pedalboard-block-reset" if self.block_reset else "pedalboard-stateful"
                )
        return result


def install_realtime_voice_dsp_output_guard_patch() -> None:
    manager_class = dsp.RealtimeVoiceDSPManager
    if getattr(manager_class, "_aliver_output_guard_patch", False):
        return

    original_make_board = manager_class._make_board
    original_status = manager_class.status

    def make_board(self: Any):
        board = original_make_board(self)
        pitch = abs(float(self._config.get("pitch_semitones") or 0.0))
        return _RealtimeBoardAdapter(self, board, block_reset=pitch >= 0.01)

    def status(self: Any) -> dict[str, Any]:
        value = original_status(self)
        input_dbfs = float(value.get("input_dbfs") or -96.0)
        output_dbfs = float(value.get("output_dbfs") or -96.0)
        input_active = input_dbfs > -70.0
        output_silent = output_dbfs <= -90.0
        value["signal_diagnosis"] = {
            "input_active": input_active,
            "output_active": output_dbfs > -70.0,
            "input_without_output": bool(input_active and output_silent),
            "message": (
                "DSP 输入已有声音，但处理后输出仍为静音。请检查处理引擎状态。"
                if input_active and output_silent
                else "DSP 输入与输出信号正常。"
                if input_active and not output_silent
                else "当前未检测到 DSP 输入声音。"
            ),
        }
        value.setdefault("processing_mode", self._state.get("processing_mode"))
        value.setdefault(
            "empty_output_recoveries",
            int(self._state.get("empty_output_recoveries") or 0),
        )
        return value

    manager_class._make_board = make_board
    manager_class.status = status
    manager_class._aliver_output_guard_patch = True
