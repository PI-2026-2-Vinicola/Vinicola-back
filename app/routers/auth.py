from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, RecoverIn, TokenOut, UserOut
from ..security import create_access_token, verify_secret

router = APIRouter(prefix="/auth", tags=["Autenticação"])


@router.post("/login", response_model=TokenOut, response_model_by_alias=True)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not user or not verify_secret(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha inválidos.")
    return TokenOut(access_token=create_access_token(user.email, user.role), user=UserOut.model_validate(user))


@router.post("/recover", status_code=status.HTTP_202_ACCEPTED)
def recover(body: RecoverIn):
    # Resposta idêntica para e-mails existentes ou não (evita enumeração de usuários).
    # Integração de envio de e-mail: SMTP/SendGrid — fora do escopo do protótipo.
    return {"detail": "Se o e-mail estiver cadastrado, enviaremos as instruções de recuperação."}


@router.get("/me", response_model=UserOut, response_model_by_alias=True)
def me(user: User = Depends(current_user)):
    return user
