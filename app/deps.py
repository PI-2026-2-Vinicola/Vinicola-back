from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Sensor, User
from .security import decode_access_token, verify_secret

bearer = HTTPBearer(auto_error=False)


def current_user_optional(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User | None:
    if not creds:
        return None
    try:
        payload = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado.")
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não encontrado.")
    return user


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    return user


def reader(user: User | None = Depends(current_user_optional)) -> User | None:
    """Leitura: pública quando PUBLIC_READ=true; caso contrário exige login."""
    if user is None and not get_settings().public_read:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_roles(*roles: str) -> Callable[[User], User]:
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Seu perfil não tem acesso a este recurso.")
        return user

    return checker


def authorize_device(sensor: Sensor, device_token: str | None, user: User | None) -> None:
    """Ingestão aceita o token do próprio dispositivo (X-Device-Token) ou um usuário autenticado."""
    if user is not None:
        return
    if device_token and verify_secret(device_token, sensor.device_token_hash):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token do dispositivo inválido.")


def device_token_header(x_device_token: str | None = Header(default=None)) -> str | None:
    return x_device_token
