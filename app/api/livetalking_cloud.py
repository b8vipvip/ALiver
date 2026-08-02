from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.bridge_hub import bridge_hub
from app.config import get_settings
from app.db import SessionLocal
from app.json_utils import loads
from app.log_service import write_log
from app.models import BridgeAgent
from app.security import decrypt_json, encrypt_json, generate_token, hash_token, verify_token

logger = logging.getLogger("aliver.livetalking_cloud")

ROOT_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
CONFIG_PATH = ROOT_DIR / "data" / "livetalking_cloud_console.json"
_CONFIG_LOCK = threading.RLock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "base_url": "",
    "avatar_id": "",
    "bridge_id": "",
    "verify_tls": True,
    "max_queue_ms": 400,
    "reconnect_min_seconds": 1.0,
    "reconnect_max_seconds": 15.0,
    "auto_start": True,
    "last_session_id": "",
}

admin_router = APIRouter(prefix="/livetalking", tags=["livetalking-cloud"])
public_router = APIRouter(tags=["livetalking-viewer"])


class LiveTalkingConfigUpdate(BaseModel):
    base_url: str = Field(default="", max_length=1000)
    token: str | None = Field(default=None, max_length=4096)
    clear_token: bool = False
    avatar_id: str = Field(default="", max_length=300)
    bridge_id: str = Field(default="", max_length=100)
    verify_tls: bool = True
    max_queue_ms: int = Field(default=400, ge=100, le=2000)
    reconnect_min_seconds: float = Field(default=1.0, ge=0.5, le=30.0)
    reconnect_max_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    auto_start: bool = True


class LiveTalkingSessionRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=500)


class ViewerSessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=500)


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LiveTalking 地址必须是有效的 http:// 或 https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("LiveTalking 地址不能包含用户名或密码")

    path = parsed.path.rstrip("/")
    for suffix in ("/aliver.html", "/api/aliver/health", "/api/aliver/pcm"):
        if path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            break
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", "")).rstrip("/")


def _ws_url(base_url: str) -> str:
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, f"{parsed.path}/api/aliver/pcm", "", ""))


def _cloud_origin(base_url: str) -> str:
    parsed = urlsplit(_normalize_base_url(base_url))
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _read_document() -> dict[str, Any]:
    with _CONFIG_LOCK:
        if not CONFIG_PATH.exists():
            return {}
        try:
            value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("Unable to read LiveTalking console configuration")
            return {}
    return value if isinstance(value, dict) else {}


def _load_config() -> dict[str, Any]:
    document = _read_document()
    raw_settings = document.get("settings")
    settings = {**DEFAULT_SETTINGS, **(raw_settings if isinstance(raw_settings, dict) else {})}
    try:
        settings["base_url"] = _normalize_base_url(str(settings.get("base_url") or ""))
    except ValueError:
        settings["base_url"] = ""
    settings["avatar_id"] = str(settings.get("avatar_id") or "").strip()
    settings["bridge_id"] = str(settings.get("bridge_id") or "").strip()
    settings["last_session_id"] = str(settings.get("last_session_id") or "").strip()
    settings["verify_tls"] = bool(settings.get("verify_tls", True))
    settings["auto_start"] = bool(settings.get("auto_start", True))
    settings["max_queue_ms"] = max(100, min(int(settings.get("max_queue_ms") or 400), 2000))
    settings["reconnect_min_seconds"] = max(
        0.5,
        min(float(settings.get("reconnect_min_seconds") or 1.0), 30.0),
    )
    settings["reconnect_max_seconds"] = max(
        settings["reconnect_min_seconds"],
        min(float(settings.get("reconnect_max_seconds") or 15.0), 120.0),
    )
    credentials = decrypt_json(str(document.get("credentials_encrypted") or ""))
    return {
        "settings": settings,
        "token": str(credentials.get("token") or ""),
        "viewer_key": str(credentials.get("viewer_key") or ""),
        "viewer_key_hash": str(document.get("viewer_key_hash") or ""),
        "updated_at": document.get("updated_at"),
    }


def _save_config(settings: dict[str, Any], token: str, viewer_key: str) -> dict[str, Any]:
    normalized = {**DEFAULT_SETTINGS, **dict(settings)}
    normalized["base_url"] = _normalize_base_url(str(normalized.get("base_url") or ""))
    normalized["avatar_id"] = str(normalized.get("avatar_id") or "").strip()
    normalized["bridge_id"] = str(normalized.get("bridge_id") or "").strip()
    normalized["last_session_id"] = str(normalized.get("last_session_id") or "").strip()
    normalized["verify_tls"] = bool(normalized.get("verify_tls", True))
    normalized["auto_start"] = bool(normalized.get("auto_start", True))
    normalized["max_queue_ms"] = max(100, min(int(normalized.get("max_queue_ms") or 400), 2000))
    normalized["reconnect_min_seconds"] = max(
        0.5,
        min(float(normalized.get("reconnect_min_seconds") or 1.0), 30.0),
    )
    normalized["reconnect_max_seconds"] = max(
        normalized["reconnect_min_seconds"],
        min(float(normalized.get("reconnect_max_seconds") or 15.0), 120.0),
    )
    viewer_key = str(viewer_key or "").strip() or generate_token()
    document = {
        "version": 1,
        "settings": normalized,
        "credentials_encrypted": encrypt_json(
            {"token": str(token or "").strip(), "viewer_key": viewer_key}
        ),
        "viewer_key_hash": hash_token(viewer_key),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with _CONFIG_LOCK:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CONFIG_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_PATH)
    return _load_config()


def _viewer_url(request: Request, viewer_key: str) -> str:
    root = str(request.base_url).rstrip("/")
    return f"{root}/api/livetalking-viewer?key={quote(viewer_key, safe='')}"


def _admin_config_out(request: Request, config: dict[str, Any]) -> dict[str, Any]:
    viewer_key = str(config.get("viewer_key") or "")
    settings = dict(config["settings"])
    base_url = str(settings.get("base_url") or "")
    return {
        "ok": True,
        "settings": settings,
        "token_configured": bool(config.get("token")),
        "viewer_key": viewer_key,
        "viewer_url": _viewer_url(request, viewer_key) if viewer_key else None,
        "console_url": f"{str(request.base_url).rstrip('/')}/api/livetalking-console",
        "cloud": {
            "health_url": f"{base_url}/api/aliver/health" if base_url else "",
            "streams_url": f"{base_url}/api/aliver/streams" if base_url else "",
            "viewer_url": f"{base_url}/aliver.html" if base_url else "",
            "ws_url": _ws_url(base_url) if base_url else "",
            "origin": _cloud_origin(base_url) if base_url else "",
        },
        "updated_at": config.get("updated_at"),
    }


def _verify_viewer_key(key: str) -> dict[str, Any]:
    config = _load_config()
    expected_hash = str(config.get("viewer_key_hash") or "")
    if not key or not expected_hash or not verify_token(key, expected_hash):
        raise HTTPException(status_code=401, detail="LiveTalking viewer key 无效")
    return config


def _validate_bridge(bridge_id: str, command_type: str) -> BridgeAgent:
    if not bridge_id:
        raise HTTPException(status_code=409, detail="尚未选择 Windows Bridge")
    with SessionLocal() as db:
        row = db.get(BridgeAgent, bridge_id)
        if not row:
            raise HTTPException(status_code=404, detail="Windows Bridge 不存在")
        capabilities = loads(row.capabilities_json, [])
        if command_type not in capabilities:
            raise HTTPException(
                status_code=409,
                detail=f"当前 Bridge 不支持 {command_type}，请先更新并重启 Bridge",
            )
        db.expunge(row)
    if not bridge_hub.is_connected(bridge_id):
        raise HTTPException(status_code=409, detail="Windows Bridge 当前离线")
    return row


def _record_bridge_command(
    bridge_id: str,
    command_type: str,
    *,
    ok: bool,
    details: dict[str, Any],
) -> None:
    try:
        with SessionLocal() as db:
            write_log(
                db,
                category="livetalking.bridge.command",
                level="INFO" if ok else "ERROR",
                message=(
                    f"LiveTalking Bridge command {'completed' if ok else 'failed'}: "
                    f"{command_type}"
                ),
                bridge_id=bridge_id,
                details=details,
            )
    except Exception:
        logger.exception("Unable to persist LiveTalking Bridge command log")


async def _send_bridge_command(
    bridge_id: str,
    command_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_bridge(bridge_id, command_type)
    timeout = max(1.0, min(float(get_settings().bridge_command_timeout), 60.0))
    try:
        result = await bridge_hub.send_command(
            bridge_id,
            command_type,
            dict(payload or {}),
            timeout,
        )
    except RuntimeError as exc:
        _record_bridge_command(
            bridge_id,
            command_type,
            ok=False,
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_bridge_command(
        bridge_id,
        command_type,
        ok=True,
        details={"result": result},
    )
    return result


def _bridge_stream_payload(config: dict[str, Any], session_id: str) -> dict[str, Any]:
    settings = config["settings"]
    base_url = str(settings.get("base_url") or "")
    token = str(config.get("token") or "")
    if not base_url:
        raise HTTPException(status_code=409, detail="尚未配置 LiveTalking 云端地址")
    if not token:
        raise HTTPException(status_code=409, detail="尚未配置 ALIVER_STREAM_TOKEN")
    if not session_id:
        raise HTTPException(status_code=409, detail="尚未获得 LiveTalking sessionid")
    return {
        "enabled": True,
        "ws_url": _ws_url(base_url),
        "token": token,
        "session_id": session_id,
        "verify_tls": bool(settings.get("verify_tls", True)),
        "max_queue_ms": int(settings.get("max_queue_ms") or 400),
        "reconnect_min_seconds": float(settings.get("reconnect_min_seconds") or 1.0),
        "reconnect_max_seconds": float(settings.get("reconnect_max_seconds") or 15.0),
    }


def _json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text[:2000]}


async def _cloud_health(config: dict[str, Any]) -> dict[str, Any]:
    settings = config["settings"]
    base_url = str(settings.get("base_url") or "")
    if not base_url:
        raise HTTPException(status_code=409, detail="尚未配置 LiveTalking 云端地址")
    verify_tls = bool(settings.get("verify_tls", True))
    timeout = httpx.Timeout(12.0, connect=6.0)
    try:
        async with httpx.AsyncClient(
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            health_response = await client.get(f"{base_url}/api/aliver/health")
            streams_response: httpx.Response | None = None
            token = str(config.get("token") or "")
            if token:
                streams_response = await client.get(
                    f"{base_url}/api/aliver/streams",
                    headers={"Authorization": f"Bearer {token}"},
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接 LiveTalking 云端：{type(exc).__name__}: {exc}",
        ) from exc
    return {
        "ok": health_response.is_success,
        "base_url": base_url,
        "health_status": health_response.status_code,
        "health": _json_body(health_response),
        "streams_status": streams_response.status_code if streams_response else None,
        "streams": _json_body(streams_response) if streams_response else None,
        "ws_url": _ws_url(base_url),
        "viewer_url": f"{base_url}/aliver.html",
    }


@admin_router.get("/config")
def get_config(request: Request) -> dict[str, Any]:
    return _admin_config_out(request, _load_config())


@admin_router.put("/config")
def update_config(payload: LiveTalkingConfigUpdate, request: Request) -> dict[str, Any]:
    current = _load_config()
    try:
        base_url = _normalize_base_url(payload.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.bridge_id:
        with SessionLocal() as db:
            if not db.get(BridgeAgent, payload.bridge_id):
                raise HTTPException(status_code=404, detail="Windows Bridge 不存在")
    token = str(current.get("token") or "")
    if payload.clear_token:
        token = ""
    elif payload.token is not None and payload.token.strip():
        token = payload.token.strip()
    settings = {
        **current["settings"],
        "base_url": base_url,
        "avatar_id": payload.avatar_id.strip(),
        "bridge_id": payload.bridge_id.strip(),
        "verify_tls": payload.verify_tls,
        "max_queue_ms": payload.max_queue_ms,
        "reconnect_min_seconds": payload.reconnect_min_seconds,
        "reconnect_max_seconds": max(
            payload.reconnect_min_seconds,
            payload.reconnect_max_seconds,
        ),
        "auto_start": payload.auto_start,
    }
    saved = _save_config(settings, token, str(current.get("viewer_key") or ""))
    return _admin_config_out(request, saved)


@admin_router.post("/viewer-key/rotate")
def rotate_viewer_key(request: Request) -> dict[str, Any]:
    current = _load_config()
    saved = _save_config(current["settings"], str(current.get("token") or ""), generate_token())
    return _admin_config_out(request, saved)


@admin_router.get("/health")
async def check_cloud_health() -> dict[str, Any]:
    return await _cloud_health(_load_config())


async def _start_or_configure(
    config: dict[str, Any],
    session_id: str,
    *,
    start: bool,
) -> dict[str, Any]:
    settings = config["settings"]
    command_type = "audio.livetalking.start" if start else "audio.livetalking.configure"
    result = await _send_bridge_command(
        str(settings.get("bridge_id") or ""),
        command_type,
        _bridge_stream_payload(config, session_id),
    )
    return {
        "ok": True,
        "command_type": command_type,
        "session_id": session_id,
        "bridge_id": settings.get("bridge_id"),
        "result": result,
    }


@admin_router.post("/bridge/configure")
async def configure_bridge(payload: LiveTalkingSessionRequest) -> dict[str, Any]:
    config = _load_config()
    session_id = str(payload.session_id or config["settings"].get("last_session_id") or "").strip()
    return await _start_or_configure(config, session_id, start=False)


@admin_router.post("/bridge/start")
async def start_bridge(payload: LiveTalkingSessionRequest) -> dict[str, Any]:
    config = _load_config()
    session_id = str(payload.session_id or config["settings"].get("last_session_id") or "").strip()
    if session_id and session_id != config["settings"].get("last_session_id"):
        config["settings"]["last_session_id"] = session_id
        config = _save_config(
            config["settings"],
            str(config.get("token") or ""),
            str(config.get("viewer_key") or ""),
        )
    return await _start_or_configure(config, session_id, start=True)


async def _simple_bridge_action(command_type: str) -> dict[str, Any]:
    config = _load_config()
    bridge_id = str(config["settings"].get("bridge_id") or "")
    result = await _send_bridge_command(bridge_id, command_type)
    return {"ok": True, "command_type": command_type, "bridge_id": bridge_id, "result": result}


@admin_router.post("/bridge/status")
async def bridge_status() -> dict[str, Any]:
    return await _simple_bridge_action("audio.livetalking.status")


@admin_router.post("/bridge/stop")
async def stop_bridge() -> dict[str, Any]:
    return await _simple_bridge_action("audio.livetalking.stop")


@admin_router.post("/bridge/interrupt")
async def interrupt_bridge() -> dict[str, Any]:
    return await _simple_bridge_action("audio.livetalking.interrupt")


@public_router.get("/livetalking-console", include_in_schema=False)
def console_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "livetalking_cloud.html")


@public_router.get("/livetalking-viewer", include_in_schema=False)
def viewer_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "livetalking_viewer.html")


@public_router.get("/livetalking-viewer/config")
def viewer_config(key: str = Query(..., min_length=1, max_length=500)) -> dict[str, Any]:
    config = _verify_viewer_key(key)
    settings = config["settings"]
    base_url = str(settings.get("base_url") or "")
    if not base_url:
        raise HTTPException(status_code=409, detail="LiveTalking 云端地址尚未配置")
    return {
        "ok": True,
        "base_url": base_url,
        "cloud_origin": _cloud_origin(base_url),
        "avatar_id": settings.get("avatar_id"),
        "auto_start": bool(settings.get("auto_start", True)),
        "video_only": True,
    }


@public_router.post("/livetalking-viewer/session")
async def viewer_session(
    payload: ViewerSessionRequest,
    key: str = Query(..., min_length=1, max_length=500),
) -> dict[str, Any]:
    config = _verify_viewer_key(key)
    session_id = payload.session_id.strip()
    config["settings"]["last_session_id"] = session_id
    config = _save_config(
        config["settings"],
        str(config.get("token") or ""),
        str(config.get("viewer_key") or ""),
    )
    auto_start = bool(config["settings"].get("auto_start", True))
    result = await _start_or_configure(config, session_id, start=auto_start)
    return {**result, "auto_started": auto_start}
