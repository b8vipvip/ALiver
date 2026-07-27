from __future__ import annotations

from app.json_utils import loads
from app.models import ProviderConfig
from app.providers import PROVIDER_CLASSES
from app.providers.base import AvatarProvider, ProviderContext
from app.security import decrypt_json


def provider_class(provider_type: str) -> type[AvatarProvider]:
    try:
        return PROVIDER_CLASSES[provider_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider type: {provider_type}") from exc


def build_provider(config: ProviderConfig) -> AvatarProvider:
    cls = provider_class(config.provider_type)
    context = ProviderContext(
        provider_id=config.id,
        name=config.name,
        provider_type=config.provider_type,
        api_base_url=config.api_base_url,
        credentials=decrypt_json(config.credentials_encrypted),
        settings=loads(config.settings_json, {}),
    )
    return cls(context)


def execution_mode(provider_type: str) -> str:
    return provider_class(provider_type).execution_mode
