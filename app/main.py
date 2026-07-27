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
from app.api import bridges, dashboard, director, health, logs, providers, sessions
from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.director_service import dispatch_queued, requeue_dispatched
from app.extension_hub import extension_hub
from app.json_utils import dumps, loads
from app.log_service import write_log
from app.models import BridgeAgent, BrowserExtension, DirectorCommand, ProviderConfig
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
    description="Local-first AI live avatar provider, director and bridge control plane",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(health.router)
app.include_router(providers.router)
app.include_router(sessions.router)
app.include_router(bridges.router)
app.include_router(director.router)
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


@app.websocket("/ws/extensions/{extension_id}")
async def extension_websocket(
    websocket: WebSocket,
    extension_id: str,
    token: str = Query(...),
) -> None:
    with SessionLocal() as db:
        row = db.get(BrowserExtension, extension_id)
        if not row or not verify_token(token, row.token_hash):
            await websocket.close(code=4401)
            return
        await extension_hub.connect(extension_id, websocket)
        row.status = "online"
        row.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        write_log(
            db,
            category="director.extension.connected",
            message=f"Chrome extension connected: {row.name}",
            details={"extension_id": row.id},
        )
        await websocket.send_json({"type": "welcome", "extension_id": extension_id})
        await dispatch_queued(db, extension_id)
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                extension = db.get(BrowserExtension, extension_id)
                if not extension:
                    await websocket.close(code=4404)
                    return
                extension.status = "online"
                extension.last_seen_at = now
                if message_type in {"heartbeat", "extension.hello", "page.status"}:
                    metadata = loads(extension.metadata_json, {})
                    incoming = message.get("metadata") or {}
                    if isinstance(incoming, dict):
                        metadata.update(incoming)
                    extension.metadata_json = dumps(metadata)
                    if message.get("url"):
                        extension.active_tab_url = str(message["url"])[:1000]
                    db.commit()
                    await websocket.send_json({"type": "pong", "at": now.isoformat()})
                elif message_type == "command.result":
                    command_id = str(message.get("command_id", ""))
                    command = db.get(DirectorCommand, command_id)
                    if command and command.extension_id == extension_id:
                        ok = bool(message.get("ok"))
                        command.status = "completed" if ok else "failed"
                        command.result_json = dumps(message.get("data") or {})
                        command.error_message = None if ok else str(
                            message.get("error") or "Unknown extension error"
                        )
                        command.completed_at = now
                        db.commit()
                        write_log(
                            db,
                            category="director.command.result",
                            level="INFO" if ok else "ERROR",
                            message=(
                                f"Director command {'completed' if ok else 'failed'}: "
                                f"{command.command_type}"
                            ),
                            details={"command_id": command.id, "extension_id": extension_id},
                        )
                        await websocket.send_json(
                            {
                                "type": "command.result.ack",
                                "command_id": command.id,
                                "status": command.status,
                            }
                        )
                else:
                    db.commit()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Extension WebSocket failed: %s", extension_id)
    finally:
        await extension_hub.disconnect(extension_id)
        with SessionLocal() as db:
            requeue_dispatched(db, extension_id)
            row = db.get(BrowserExtension, extension_id)
            if row:
                row.status = "offline"
                db.commit()
                write_log(
                    db,
                    category="director.extension.disconnected",
                    message=f"Chrome extension disconnected: {row.name}",
                    details={"extension_id": row.id},
                )


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
