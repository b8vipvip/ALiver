__version__ = "0.16.3"

# Install compatibility fixes before app.main imports workers and API routers.
from app import pro_director_runtime_patch as _pro_director_runtime_patch  # noqa: E402,F401
from app.bridge_session_restore_patch import install_bridge_session_restore_patch  # noqa: E402

# Voice timbre is now handled by the Windows Bridge real-time DSP chain. Do not
# decorate director prompts with the legacy text-only voice tuning instructions.
install_bridge_session_restore_patch()
