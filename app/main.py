from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import bridges, dashboard, health, logs, providers, sessions
from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.json_utils import dumps
from app.log_service import write_log
from app.models import BridgeAgent, ProviderConfig
from app.security import encrypt_json, verify_token

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger("aliver")

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        if db.query(ProviderConfig).count() == 0:
            mock = ProviderConfig(
                name="Local Mock",
                provider_type="mock",
                enabled=True,
                credentials_encrypted=encrypt_json({}),
                settings_json=dumps({}),
            )
            db.add(mock)
            db.commit()
            write_log(
                db,
                category="system.seed",
                message="Created the default Local Mock provider",
                provider_id=mock.id,
            )
    logger.info("ALiver started on %s:%s", settings.host, settings.port)
    yield
    logger.info("ALiver stopping")


app = FastAPI(
    title="ALiver",
    version=__version__,
    description="Local-first AI live avatar provider and bridge control plane",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(health.router)
app.include_router(providers.router)
app.include_router(sessions.router)
app.include_router(bridges.router)
app.include_router(logs.router)
app.include_router(dashboard.router)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (BASE_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.websocket("/ws/bridges/{bridge_id}")
async def bridge_websocket(
    websocket: WebSocket,
    bridge_id: str,
    token: str = Query(...),
) -> None:
    with SessionLocal() as db:
        row = db.get(BridgeAgent, bridge_id)
        if not row or not verify_token(token, row.token_hash):
            await websocket.close(code=4401)
            return
        await bridge_hub.connect(bridge_id, websocket)
        row.status = "online"
        row.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        write_log(
            db,
            category="bridge.connected",
            message=f"Bridge connected: {row.name}",
            bridge_id=row.id,
        )
    try:
        await websocket.send_json({"type": "welcome", "bridge_id": bridge_id})
        while True:
            message = await websocket.receive_json()
            await bridge_hub.handle_message(bridge_id, message)
            if message.get("type") == "heartbeat":
                with SessionLocal() as db:
                    row = db.get(BridgeAgent, bridge_id)
                    if row:
                        row.status = "online"
                        row.last_seen_at = datetime.now(timezone.utc)
                        if isinstance(message.get("metadata"), dict):
                            row.metadata_json = dumps(message["metadata"])
                        db.commit()
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Bridge WebSocket failed: %s", bridge_id)
    finally:
        await bridge_hub.disconnect(bridge_id)
        with SessionLocal() as db:
            row = db.get(BridgeAgent, bridge_id)
            if row:
                row.status = "offline"
                db.commit()
                write_log(
                    db,
                    category="bridge.disconnected",
                    message=f"Bridge disconnected: {row.name}",
                    bridge_id=row.id,
                )


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
