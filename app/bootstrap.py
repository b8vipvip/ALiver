from __future__ import annotations

import uvicorn

import app

SERVER_VERSION = "0.14.3"

app.__version__ = SERVER_VERSION
from app import main as app_main  # noqa: E402

application = app_main.app
settings = app_main.settings
# A first-time VTube Studio token request intentionally waits for the user to
# approve the plugin inside VTube Studio. Keep the server-to-Bridge request
# alive even when an older .env still contains the previous 30-second value.
settings.bridge_command_timeout = max(float(settings.bridge_command_timeout), 240.0)


@application.middleware("http")
async def disable_console_asset_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


if __name__ == "__main__":
    uvicorn.run(application, host=settings.host, port=settings.port, reload=False)
