__version__ = "0.15.0"

# Install compatibility fixes before app.main imports the auto-director worker.
from app import pro_director_runtime_patch as _pro_director_runtime_patch  # noqa: E402,F401
from app.voice_director_patch import install_voice_director_patch  # noqa: E402

install_voice_director_patch()
