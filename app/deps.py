from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Sensor, User
from .security import decode_access_token, verify_secret

bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return (forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "")) or "-"


def current_user_optional(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User | None:
    if not creds:
        return None
    try:
        payload = decode_access_token(creds.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada ou inválida. Entre novamente.")
    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inexistente ou desativado.")
    return user


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    return user


def reader(user: User | None = Depends(current_user_optional)) -> User | None:
    """Leitura de dados: exige login, exceto quando PUBLIC_READ=true (demonstrações)."""
    if user is None and not get_settings().public_read:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_roles(*roles: str) -> Callable[[User], User]:
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Seu perfil não tem permissão para esta ação.")
        return user

    return checker


def authorize_device(sensor: Sensor, device_token: str | None, user: User | None) -> None:
    """Dados de dispositivo aceitam o token do próprio sensor (X-Device-Token) ou um usuário autenticado."""
    if user is not None:
        return
    if device_token and verify_secret(device_token, sensor.device_token_hash):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token do dispositivo inválido.")


def device_token_header(x_device_token: str | None = Header(default=None)) -> str | None:
    return x_device_token
