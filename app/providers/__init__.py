from app.providers.akool import AkoolProvider
from app.providers.liveavatar import LiveAvatarProvider
from app.providers.mock import MockProvider
from app.providers.simli import SimliProvider
from app.providers.tavus import TavusProvider

PROVIDER_CLASSES = {
    "mock": MockProvider,
    "tavus": TavusProvider,
    "akool": AkoolProvider,
    "liveavatar": LiveAvatarProvider,
    "simli": SimliProvider,
}
