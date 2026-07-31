from __future__ import annotations

from sqlalchemy import select

from app.native_voice_tuning import profile_native_tuning, render_native_instruction
from app.voice_models import VoiceProfile


def install_live_run_native_voice_patch() -> None:
    from app.live_run_service import LiveRunRecorder

    if getattr(LiveRunRecorder, "_aliver_native_voice_snapshot_patch", False):
        return
    original = LiveRunRecorder._configuration_snapshot

    def configuration_snapshot(self, db):
        value = original(self, db)
        profiles = {
            row.id: row for row in db.scalars(select(VoiceProfile)).all()
        }
        for item in value.get("voice_profiles") or []:
            row = profiles.get(str(item.get("id") or ""))
            if row is None:
                continue
            item["native_tuning"] = profile_native_tuning(row)
            item["rendered_native_instruction"] = render_native_instruction(row)
            item["voice_pipeline"] = "chatgpt_live_native"
        return value

    LiveRunRecorder._configuration_snapshot = configuration_snapshot
    LiveRunRecorder._aliver_native_voice_snapshot_patch = True
