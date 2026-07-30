__version__ = "0.12.1"

# Install compatibility fixes before app.main imports the auto-director worker.
from app import pro_director_runtime_patch as _pro_director_runtime_patch  # noqa: E402,F401
