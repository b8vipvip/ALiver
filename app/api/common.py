from __future__ import annotations

from typing import Any

from app.json_utils import loads
from app.models import AvatarSession, BridgeAgent, EventLog, ProviderConfig
from app.provider_manager import execution_mode
from app.schemas import BridgeOut, LogOut, ProviderOut, SessionOut
from app.security import decrypt_json


def provider_to_out(row: ProviderConfig) -> ProviderOut:
    credentials = decrypt_json(row.credentials_encrypted)
    return ProviderOut(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        enabled=row.enabled,
        api_base_url=row.api_base_url,
        credential_keys=sorted(credentials.keys()),
        settings=loads(row.settings_json, {}),
        execution_mode=execution_mode(row.provider_type),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def session_to_out(row: AvatarSession, provider: ProviderConfig | None = None) -> SessionOut:
    return SessionOut(
        id=row.id,
        provider_config_id=row.provider_config_id,
        provider_name=provider.name if provider else None,
        provider_type=provider.provider_type if provider else None,
        bridge_id=row.bridge_id,
        status=row.status,
        external_session_id=row.external_session_id,
        request=loads(row.request_json, {}),
        response=loads(row.response_json, {}),
        error_message=row.error_message,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def bridge_to_out(row: BridgeAgent, connected: bool) -> BridgeOut:
    # Database status can remain "online" after an unclean server/Bridge stop.
    # The active WebSocket hub is the only authoritative live connection state.
    return BridgeOut(
        id=row.id,
        name=row.name,
        machine_name=row.machine_name,
        version=row.version,
        capabilities=loads(row.capabilities_json, []),
        metadata=loads(row.metadata_json, {}),
        status="online" if connected else "offline",
        connected=connected,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def log_to_out(row: EventLog) -> LogOut:
    return LogOut(
        id=row.id,
        level=row.level,
        category=row.category,
        message=row.message,
        details=loads(row.details_json, {}),
        provider_id=row.provider_id,
        session_id=row.session_id,
        bridge_id=row.bridge_id,
        latency_ms=row.latency_ms,
        created_at=row.created_at,
    )


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    value = dict(base)
    value.update(override)
    return value
