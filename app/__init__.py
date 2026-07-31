__version__ = "0.15.1"

# Install compatibility fixes before app.main imports workers and API routers.
from app import pro_director_runtime_patch as _pro_director_runtime_patch  # noqa: E402,F401
from app.bridge_session_restore_patch import install_bridge_session_restore_patch  # noqa: E402
from app.native_voice_tuning import install_native_voice_patch  # noqa: E402

install_native_voice_patch()
install_bridge_session_restore_patch()

from app.voice_director_patch import install_voice_director_patch  # noqa: E402

install_voice_director_patch()
