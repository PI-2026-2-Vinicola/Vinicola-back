"""Organizações (exportadoras): o administrador cadastra; os demais perfis consultam só a própria."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import client_ip
from ..models import InternationalClient, Organization, Sensor, User
from ..rbac import check_organization, require, tenant
from ..schemas import OrganizationIn, OrganizationOut, OrganizationUpdateIn
from ..services.audit import audit

router = APIRouter(prefix="/organizations", tags=["Organizações"])


def _counts(db: Session, model, ids: list[int]) -> dict[int, int]:
    return dict(db.query(model.organization_id, func.count(model.id)).filter(model.organization_id.in_(ids)).group_by(model.organization_id).all())


def _out(db: Session, orgs: list[Organization]) -> list[OrganizationOut]:
    ids = [o.id for o in orgs]
    users, sensors, clients = _counts(db, User, ids), _counts(db, Sensor, ids), _counts(db, InternationalClient, ids)
    return [
        OrganizationOut(id=o.id, name=o.name, city=o.city, active=o.active, users=users.get(o.id, 0), sensors=sensors.get(o.id, 0),
                        clients=clients.get(o.id, 0), created_at=o.created_at)
        for o in orgs
    ]


def get_organization(db: Session, organization_id: int) -> Organization:
    org = db.get(Organization, organization_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Organização {organization_id} não encontrada.")
    return org


@router.get("", response_model=list[OrganizationOut], response_model_by_alias=True)
def list_organizations(db: Session = Depends(get_db), user: User = Depends(require("organizacoes", "R"))):
    q = db.query(Organization).order_by(Organization.name)
    scope = tenant(user)
    if scope is not None:
        q = q.filter(Organization.id == scope)
    return _out(db, q.all())


@router.get("/{organization_id}", response_model=OrganizationOut, response_model_by_alias=True)
def read_organization(organization_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("organizacoes", "R"))):
    org = get_organization(db, organization_id)
    check_organization(db, request, user, org.id, "organizacoes", str(org.id))
    return _out(db, [org])[0]


@router.post("", response_model=OrganizationOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def create_organization(body: OrganizationIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("organizacoes", "C"))):
    org = Organization(name=body.name.strip(), city=(body.city or "").strip() or None)
    db.add(org)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Já existe uma organização chamada {body.name}.")
    audit(db, "organizacao_criada", actor=admin.email, target=org.name, ip=client_ip(request))
    return _out(db, [org])[0]


@router.patch("/{organization_id}", response_model=OrganizationOut, response_model_by_alias=True)
def update_organization(organization_id: int, body: OrganizationUpdateIn, request: Request, db: Session = Depends(get_db),
                        admin: User = Depends(require("organizacoes", "U"))):
    org = get_organization(db, organization_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if value is not None:
            setattr(org, key, value.strip() if isinstance(value, str) else value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Já existe uma organização com esse nome.")
    audit(db, "organizacao_alterada", actor=admin.email, target=org.name, details=changes, ip=client_ip(request))
    return _out(db, [org])[0]


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(organization_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("organizacoes", "D"))):
    org = get_organization(db, organization_id)
    out = _out(db, [org])[0]
    if out.users or out.sensors or out.clients:
        raise HTTPException(status.HTTP_409_CONFLICT, "A organização tem usuários, sensores ou clientes. Remova-os ou desative a organização.")
    db.delete(org)
    audit(db, "organizacao_excluida", actor=admin.email, target=org.name, ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
