"""
Clientes internacionais (importadores) de cada exportadora — dados sensíveis cifrados em repouso.

Contato, e-mail, telefone, identificação fiscal e condições comerciais são gravados com AES-256-GCM
(app/crypto.py). A listagem devolve esses campos mascarados; o detalhe os decifra para quem tem
permissão e registra a consulta na auditoria. O banco nunca guarda o texto original.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..crypto import DecryptionError, decrypt, encrypt
from ..database import get_db
from ..deps import client_ip
from ..models import InternationalClient, User
from ..rbac import deny, check_organization, require, tenant
from ..schemas import ClientIn, ClientOut, ClientUpdateIn
from ..seed import default_organization
from ..services.audit import audit
from .organizations import get_organization

router = APIRouter(prefix="/clients", tags=["Clientes internacionais"])
log = logging.getLogger("oasis.clients")

SENSITIVE = ("contact_name", "email", "phone", "tax_id", "commercial_terms")
FIELD_LABEL = {"contact_name": "contato", "email": "e-mail", "phone": "telefone", "tax_id": "identificação fiscal", "commercial_terms": "condições comerciais"}


def context(organization_id: int, field: str) -> str:
    """Dado associado da cifra: o valor só decifra neste campo e nesta organização."""
    return f"international_clients.{field}:org={organization_id}"


def _mask(field: str, value: str | None) -> str | None:
    if not value:
        return value
    if field == "email":
        user, _, domain = value.partition("@")
        return f"{user[:2]}•••@{domain}"
    if field in ("phone", "tax_id"):
        digits = value[-3:]
        return "•••• " + digits
    if field == "contact_name":
        return value.split()[0][:1] + "•••"
    return "•••• (cifrado)"


def _plain(c: InternationalClient, field: str) -> str | None:
    try:
        return decrypt(getattr(c, f"{field}_enc"), context(c.organization_id, field))
    except DecryptionError:
        log.exception("Campo %s do cliente %s não pôde ser decifrado", field, c.id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Dado sensível ilegível: chave de criptografia ausente ou valor adulterado.")


def to_out(c: InternationalClient, *, full: bool) -> ClientOut:
    values = {f: _plain(c, f) for f in SENSITIVE}
    if not full:
        values = {f: _mask(f, v) for f, v in values.items()}
    return ClientOut(
        id=c.id, organization_id=c.organization_id, organization_name=c.organization.name if c.organization else None,
        company_name=c.company_name, country=c.country, masked=not full, protected_fields=list(SENSITIVE),
        active=c.active, created_at=c.created_at, updated_at=c.updated_at, **values,
    )


def _seal(c: InternationalClient, values: dict) -> None:
    for field in SENSITIVE:
        if field in values:
            raw = (values[field] or "").strip() or None
            setattr(c, f"{field}_enc", encrypt(raw, context(c.organization_id, field)))


def _get_client(db: Session, request: Request, user: User, client_id: int) -> InternationalClient:
    c = db.get(InternationalClient, client_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Cliente {client_id} não encontrado.")
    check_organization(db, request, user, c.organization_id, "clientes", str(c.id))
    if user.role == "cliente" and user.client_id != c.id:
        raise deny(db, request, user, "Acesso negado: o perfil cliente só consulta o próprio cadastro.",
                    {"recurso": "clientes", "registro": c.id, "cliente_do_usuario": user.client_id})
    return c


@router.get("", response_model=list[ClientOut], response_model_by_alias=True)
def list_clients(
    organization_id: int | None = Query(default=None, alias="organizationId"),
    db: Session = Depends(get_db),
    user: User = Depends(require("clientes", "R")),
):
    """Clientes alcançados pelo usuário, com os campos sensíveis mascarados."""
    q = db.query(InternationalClient).order_by(InternationalClient.company_name)
    scope = tenant(user)
    if scope is not None:
        q = q.filter(InternationalClient.organization_id == scope)
    elif organization_id:
        q = q.filter(InternationalClient.organization_id == organization_id)
    if user.role == "cliente":
        q = q.filter(InternationalClient.id == (user.client_id or -1))
    return [to_out(c, full=False) for c in q.all()]


@router.get("/{client_id}", response_model=ClientOut, response_model_by_alias=True)
def read_client(client_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clientes", "R"))):
    """Cadastro completo, com os campos sensíveis decifrados. Cada consulta fica na auditoria."""
    c = _get_client(db, request, user, client_id)
    out = to_out(c, full=True)
    audit(db, "dados_sensiveis_consultados", actor=user.email, target=f"cliente {c.id}", details={"empresa": c.company_name}, ip=client_ip(request))
    return out


@router.post("", response_model=ClientOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def create_client(body: ClientIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clientes", "C"))):
    scope = tenant(user)
    if scope is None:
        org = get_organization(db, body.organization_id) if body.organization_id else default_organization(db)
    else:
        if body.organization_id and body.organization_id != scope:
            check_organization(db, request, user, body.organization_id, "clientes", "novo cliente")
        org = get_organization(db, scope)
    c = InternationalClient(organization_id=org.id, company_name=body.company_name.strip(), country=body.country.strip(), created_by=user.id)
    _seal(c, body.model_dump())
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.company_name} já é cliente de {org.name}.")
    audit(db, "cliente_criado", actor=user.email, target=f"cliente {c.id}", details={"empresa": c.company_name, "organizacao": org.name}, ip=client_ip(request))
    db.refresh(c)
    return to_out(c, full=True)


@router.patch("/{client_id}", response_model=ClientOut, response_model_by_alias=True)
def update_client(client_id: int, body: ClientUpdateIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clientes", "U"))):
    c = _get_client(db, request, user, client_id)
    changes = body.model_dump(exclude_unset=True)
    for key in ("company_name", "country", "active"):
        if changes.get(key) is not None:
            setattr(c, key, changes[key].strip() if isinstance(changes[key], str) else changes[key])
    _seal(c, {k: v for k, v in changes.items() if k in SENSITIVE})
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe um cliente com esse nome nesta organização.")
    audit(db, "cliente_alterado", actor=user.email, target=f"cliente {c.id}",
          details={"campos": [FIELD_LABEL.get(k, k) for k in changes]}, ip=client_ip(request))
    db.refresh(c)
    return to_out(c, full=True)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("clientes", "D"))):
    c = _get_client(db, request, user, client_id)
    db.query(User).filter(User.client_id == c.id).update({User.client_id: None}, synchronize_session=False)
    db.delete(c)
    audit(db, "cliente_excluido", actor=user.email, target=f"cliente {c.id}", details={"empresa": c.company_name}, ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
