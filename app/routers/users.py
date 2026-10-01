from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import client_ip, require_roles
from ..models import User
from ..schemas import UserCreateIn, UserOut, UserUpdateIn
from ..security import hash_secret
from ..services.audit import audit

router = APIRouter(prefix="/users", tags=["Usuários"])
admin_only = require_roles("admin")


@router.get("", response_model=list[UserOut], response_model_by_alias=True)
def list_users(db: Session = Depends(get_db), _: User = Depends(admin_only)):
    return db.query(User).order_by(User.is_active.desc(), User.name).all()


@router.post("", response_model=UserOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um usuário com este e-mail.")
    user = User(name=body.name.strip(), email=body.email, role=body.role, password_hash=hash_secret(body.password), is_active=True)
    db.add(user)
    audit(db, "usuario_criado", actor=admin.email, target=user.email, details={"perfil": body.role}, ip=client_ip(request))
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut, response_model_by_alias=True)
def update_user(user_id: int, body: UserUpdateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado.")
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (changes.get("is_active") is False or changes.get("role", "admin") != "admin"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode desativar nem rebaixar o próprio usuário.")
    if (changes.get("is_active") is False or changes.get("role", user.role) != "admin") and user.role == "admin":
        admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True), User.id != user.id).count()
        if admins == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "O sistema precisa de ao menos um administrador ativo.")
    if "name" in changes and changes["name"]:
        user.name = changes["name"].strip()
    if "role" in changes and changes["role"]:
        user.role = changes["role"]
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
    if changes.get("password"):
        user.password_hash = hash_secret(changes["password"])
    details = {k: v for k, v in changes.items() if k != "password"} | ({"senha": "redefinida"} if changes.get("password") else {})
    audit(db, "usuario_alterado", actor=admin.email, target=user.email, details=details, ip=client_ip(request))
    db.refresh(user)
    return user
