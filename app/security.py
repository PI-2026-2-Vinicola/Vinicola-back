"""Hash de senhas (PBKDF2-SHA256), JWT de usuários e tokens de dispositivos."""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

_ITERATIONS = 240_000


def hash_secret(secret: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def hash_token(token: str) -> str:
    """Tokens de dispositivo são aleatórios (192 bits): SHA-256 basta e mantém a ingestão rápida."""
    return "sha256$" + hashlib.sha256(token.encode()).hexdigest()


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored:
        return False
    if stored.startswith("sha256$"):
        return hmac.compare_digest(hash_token(secret), stored)
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def new_device_token() -> str:
    """Token aleatório entregue uma única vez ao administrador e gravado apenas como hash."""
    return "oasis_" + secrets.token_urlsafe(24)


def new_password() -> str:
    return secrets.token_urlsafe(10) + "9a"


def create_access_token(subject: str, role: str) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = settings.jwt_expires_minutes * 60
    payload = {"sub": subject, "role": role, "iat": now, "exp": now + timedelta(seconds=expires)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256"), expires


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
