import base64
import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    secret = get_settings().secret_key.encode("utf-8")
    digest = hashlib.sha256(secret).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(value: dict[str, Any]) -> str:
    if not value:
        return ""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        raw = _fernet().decrypt(value.encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}


def generate_token() -> str:
    return secrets.token_urlsafe(36)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_token(token), expected_hash)
