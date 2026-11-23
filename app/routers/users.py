"""Usuários: só o administrador cadastra. Cada perfil, exceto o administrador, pertence a uma organização."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import client_ip
from ..models import InternationalClient, User
from ..rbac import require
from ..schemas import UserCreateIn, UserOut, UserUpdateIn
from ..security import hash_secret
from ..seed import default_organization
from ..services.audit import audit
from .organizations import get_organization

router = APIRouter(prefix="/users", tags=["Usuários"])


def _membership(db: Session, role: str, organization_id: int | None, client_id: int | None) -> tuple[int | None, int | None]:
    """Organização e cliente válidos para o perfil: admin sem organização; cliente ligado a um cliente da organização."""
    if role == "admin":
        return None, None
    org = get_organization(db, organization_id) if organization_id else default_organization(db)
    if role != "cliente":
        return org.id, None
    if not client_id:
        raise HTTPException(422, "Informe o cliente internacional do usuário de perfil cliente.")
    client = db.get(InternationalClient, client_id)
    if not client or client.organization_id != org.id:
        raise HTTPException(422, "O cliente internacional informado não pertence à organização do usuário.")
    return org.id, client.id


@router.get("", response_model=list[UserOut], response_model_by_alias=True)
def list_users(organization_id: int | None = Query(default=None, alias="organizationId"), db: Session = Depends(get_db),
               _: User = Depends(require("usuarios", "R"))):
    q = db.query(User)
    if organization_id:
        q = q.filter(User.organization_id == organization_id)
    return q.order_by(User.is_active.desc(), User.name).all()


@router.post("", response_model=UserOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("usuarios", "C"))):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um usuário com este e-mail.")
    org_id, client_id = _membership(db, body.role, body.organization_id, body.client_id)
    user = User(name=body.name.strip(), email=body.email, role=body.role, password_hash=hash_secret(body.password), is_active=True,
                organization_id=org_id, client_id=client_id)
    db.add(user)
    audit(db, "usuario_criado", actor=admin.email, target=user.email, details={"perfil": body.role, "organizacao": org_id}, ip=client_ip(request))
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut, response_model_by_alias=True)
def update_user(user_id: int, body: UserUpdateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("usuarios", "U"))):
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
    if {"role", "organization_id", "client_id"} & changes.keys():
        role = changes.get("role") or user.role
        org = changes["organization_id"] if "organization_id" in changes else user.organization_id
        client = changes["client_id"] if "client_id" in changes else user.client_id
        user.role = role
        user.organization_id, user.client_id = _membership(db, role, org, client)
    if "is_active" in changes and changes["is_active"] is not None:
        user.is_active = changes["is_active"]
    if changes.get("password"):
        user.password_hash = hash_secret(changes["password"])
    details = {k: v for k, v in changes.items() if k != "password"} | ({"senha": "redefinida"} if changes.get("password") else {})
    audit(db, "usuario_alterado", actor=admin.email, target=user.email, details=details, ip=client_ip(request))
    db.refresh(user)
    return user
