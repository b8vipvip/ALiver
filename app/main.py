from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    avatar_actions,
    auto_director,
    bridges,
    dashboard,
    director,
    douyin_live,
    health,
    live_runs,
    livetalking_cloud,
    logs,
    providers,
    sessions,
    voice,
)
from app.auth import require_admin_token
from app.auto_director_service import auto_director_worker
from app.avatar_action_service import schedule_chatgpt_status
from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.director_service import dispatch_queued, requeue_dispatched
from app.extension_hub import extension_hub
from app.json_utils import dumps, loads
from app.live_run_service import live_run_recorder, live_run_worker
from app.log_service import write_log
from app.models import BridgeAgent, BrowserExtension, DirectorCommand, ProviderConfig
from app.security import encrypt_json, verify_token
from app.session_reconciliation import reconcile_bridge_sessions
from app.voice_service import handle_assistant_completed

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
    auto_director_stop = asyncio.Event()
    auto_director_task = asyncio.create_task(auto_director_worker(auto_director_stop))
    live_run_stop = asyncio.Event()
    live_run_task = asyncio.create_task(live_run_worker(live_run_stop))
    logger.info("ALiver started on %s:%s", settings.host, settings.port)
    yield
    auto_director_stop.set()
    live_run_stop.set()
    await asyncio.gather(auto_director_task, live_run_task, return_exceptions=True)
    with SessionLocal() as db:
        if live_run_recorder.status().get("active"):
            live_run_recorder.stop(db, reason="server_shutdown")
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
app.include_router(avatar_actions.router)
app.include_router(bridges.router)
app.include_router(director.router)
app.include_router(auto_director.router)
app.include_router(douyin_live.router)
app.include_router(live_runs.router)
app.include_router(voice.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
app.include_router(
    livetalking_cloud.admin_router,
    prefix="/api/dashboard",
    dependencies=[Depends(require_admin_token)],
)
app.include_router(livetalking_cloud.public_router, prefix="/api")


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
    connection_id: str | None = None
    disconnect_code: int | None = None
    disconnect_reason: str | None = None
    with SessionLocal() as db:
        row = db.get(BridgeAgent, bridge_id)
        if not row or not verify_token(token, row.token_hash):
            await websocket.close(code=4401)
            return
        connection_id = await bridge_hub.connect(bridge_id, websocket)
        row.status = "online"
        row.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        write_log(
            db,
            category="bridge.connected",
            message=f"Bridge connected: {row.name}",
            bridge_id=row.id,
            details={"connection_id": connection_id},
        )
    try:
        await websocket.send_json(
            {"type": "welcome", "bridge_id": bridge_id, "connection_id": connection_id}
        )
        while True:
            message = await websocket.receive_json()
            await bridge_hub.handle_message(
                bridge_id,
                message,
                connection_id=connection_id,
            )
            if message.get("type") == "heartbeat":
                metadata = message.get("metadata")
                with SessionLocal() as db:
                    row = db.get(BridgeAgent, bridge_id)
                    if row:
                        row.status = "online"
                        row.last_seen_at = datetime.now(timezone.utc)
                        if isinstance(metadata, dict):
                            row.metadata_json = dumps(metadata)
                        db.commit()
                        changes = reconcile_bridge_sessions(db, bridge_id, metadata)
                        for change in changes:
                            write_log(
                                db,
                                category="session.reconciled",
                                level="ERROR",
                                message="检测到服务端会话与 Bridge 本地运行状态不一致，已标记为中断",
                                bridge_id=bridge_id,
                                session_id=change["session_id"],
                                details=change,
                            )
                await websocket.send_json(
                    {
                        "type": "pong",
                        "connection_id": connection_id,
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except WebSocketDisconnect as exc:
        disconnect_code = exc.code
        disconnect_reason = getattr(exc, "reason", None)
    except Exception as exc:
        disconnect_reason = f"{type(exc).__name__}: {exc}"
        logger.exception("Bridge WebSocket failed: %s / %s", bridge_id, connection_id)
    finally:
        removed_current = await bridge_hub.disconnect(bridge_id, connection_id)
        if removed_current:
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
                        details={
                            "connection_id": connection_id,
                            "code": disconnect_code,
                            "reason": disconnect_reason,
                        },
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
            if message_type == "assistant.completed":
                data = message.get("data") or {}
                live_run_recorder.record_external(
                    "chatgpt.assistant.completed",
                    {
                        "extension_id": extension_id,
                        "message_id": data.get("message_id"),
                        "text": data.get("text"),
                        "url": data.get("url"),
                        "observed_at": data.get("observed_at"),
                    },
                )
                asyncio.create_task(handle_assistant_completed(extension_id, dict(data)))
                await websocket.send_json(
                    {
                        "type": "assistant.completed.ack",
                        "message_id": data.get("message_id"),
                        "at": now.isoformat(),
                    }
                )
                continue
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
                        reported_version = incoming.get("extension_version")
                        if reported_version:
                            extension.version = str(reported_version)[:40]
                        if message_type == "page.status" and "generating" in incoming:
                            schedule_chatgpt_status(
                                extension_id,
                                generating=bool(incoming.get("generating")),
                            )
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
