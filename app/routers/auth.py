import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import client_ip, current_user
from ..models import User
from ..schemas import LoginIn, PasswordChangeIn, TokenOut, UserOut
from ..security import create_access_token, hash_secret, verify_secret
from ..services.audit import audit
from ..services.timeutil import utcnow

router = APIRouter(prefix="/auth", tags=["Autenticação"])

# Tentativas falhas por e-mail e por IP (processo único; em várias instâncias, usar Redis).
_failures: dict[str, deque] = defaultdict(deque)


def _locked(key: str) -> bool:
    s = get_settings()
    window = s.login_lock_minutes * 60
    q = _failures[key]
    now = time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    return len(q) >= s.login_max_attempts


def reset_login_attempts() -> None:
    _failures.clear()


@router.post("/login", response_model=TokenOut, response_model_by_alias=True)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip = client_ip(request)
    keys = (f"email:{email}", f"ip:{ip}")
    if any(_locked(k) for k in keys):
        audit(db, "login_bloqueado", actor=email, ip=ip)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Muitas tentativas. Tente novamente em {get_settings().login_lock_minutes} minutos.")
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_secret(body.password, user.password_hash) or not user.is_active:
        for k in keys:
            _failures[k].append(time.monotonic())
        audit(db, "login_falhou", actor=email, ip=ip, details={"motivo": "desativado" if user and not user.is_active else "credenciais"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha inválidos.")
    for k in keys:
        _failures.pop(k, None)
    user.last_login_at = utcnow()
    token, expires = create_access_token(user.email, user.role)
    audit(db, "login", actor=user.email, ip=ip)
    return TokenOut(access_token=token, expires_in=expires, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut, response_model_by_alias=True)
def me(user: User = Depends(current_user)):
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChangeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_secret(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha atual incorreta.")
    user.password_hash = hash_secret(body.new_password)
    audit(db, "senha_alterada", actor=user.email, target=user.email, ip=client_ip(request))
