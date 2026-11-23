"""
Dados iniciais obrigatórios: catálogo de variedades e o primeiro administrador.
Nenhum sensor ou leitura é criado automaticamente — os dados vêm dos dispositivos,
de envios manuais ou de importação. Dados de demonstração existem apenas via
`python -m app.cli seed-demo` (app/demo.py) e ficam marcados como tal.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .catalog import VARIETIES
from .config import get_settings
from .models import ImportJob, Organization, Sensor, User, Variety
from .security import hash_secret, new_password
from .services.audit import audit

log = logging.getLogger("oasis.seed")


def seed_catalog(db: Session) -> int:
    """Insere as variedades que ainda não existem (idempotente)."""
    existing = {vid for (vid,) in db.query(Variety.id)}
    missing = [Variety(**v) for v in VARIETIES if v["id"] not in existing]
    db.add_all(missing)
    db.commit()
    return len(missing)


def bootstrap_admin(db: Session) -> str | None:
    """
    Cria o primeiro administrador quando o banco não tem usuários.
    A senha vem de OASIS_ADMIN_PASSWORD; se vazia, uma senha aleatória é gerada e exibida uma única vez.
    Retorna a senha gerada (ou None).
    """
    if db.query(User.id).first():
        return None
    settings = get_settings()
    password = settings.oasis_admin_password or new_password()
    generated = not settings.oasis_admin_password
    db.add(User(name=settings.oasis_admin_name, email=settings.oasis_admin_email.lower(), role="admin", password_hash=hash_secret(password)))
    audit(db, "usuario_criado", actor="sistema", target=settings.oasis_admin_email.lower(), details={"perfil": "admin", "origem": "inicializacao"})
    if generated:
        banner = "=" * 64
        log.warning(
            "\n%s\n OASIS — administrador inicial criado\n e-mail: %s\n senha:  %s\n Guarde esta senha e altere-a no primeiro acesso (Minha conta).\n%s",
            banner, settings.oasis_admin_email, password, banner,
        )
    else:
        log.info("Administrador inicial %s criado com a senha definida em OASIS_ADMIN_PASSWORD.", settings.oasis_admin_email)
    return password if generated else None


def default_organization(db: Session) -> Organization:
    """A organização mais antiga: recebe registros criados sem organização informada."""
    org = db.query(Organization).order_by(Organization.id).first()
    if org is None:
        org = Organization(name=get_settings().oasis_farm_name.strip() or "Organização padrão")
        db.add(org)
        db.flush()
    return org


def ensure_default_organization(db: Session) -> Organization:
    """
    Garante que exista ao menos uma organização e que nenhum sensor, usuário não administrador ou
    importação fique sem organização (bancos criados antes do isolamento por organização). Idempotente.
    """
    org = default_organization(db)
    moved = db.query(Sensor).filter(Sensor.organization_id.is_(None)).update({Sensor.organization_id: org.id}, synchronize_session=False)
    moved += db.query(User).filter(User.organization_id.is_(None), User.role != "admin").update({User.organization_id: org.id}, synchronize_session=False)
    db.query(ImportJob).filter(ImportJob.organization_id.is_(None)).update({ImportJob.organization_id: org.id}, synchronize_session=False)
    db.commit()
    if moved:
        log.info("Registros sem organização associados a %s (%d).", org.name, moved)
    return org
