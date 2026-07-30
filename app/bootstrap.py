from __future__ import annotations

import uvicorn

import app

SERVER_VERSION = "0.12.1"

app.__version__ = SERVER_VERSION
from app import main as app_main  # noqa: E402

application = app_main.app
settings = app_main.settings
# A first-time VTube Studio token request intentionally waits for the user to
# approve the plugin inside VTube Studio. Keep the server-to-Bridge request
# alive even when an older .env still contains the previous 30-second value.
settings.bridge_command_timeout = max(float(settings.bridge_command_timeout), 150.0)


if __name__ == "__main__":
    uvicorn.run(application, host=settings.host, port=settings.port, reload=False)
