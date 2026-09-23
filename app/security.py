"""Hash de senhas (PBKDF2-SHA256, biblioteca padrão), JWT de usuários e tokens de dispositivos."""

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


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "role": role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expires_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
