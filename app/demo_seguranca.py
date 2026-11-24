"""
Cenário da demonstração do controle de acesso: duas exportadoras, cada uma com um sensor, um gestor e
um operador, para mostrar as permissões por perfil e o isolamento entre organizações.

    python -m app.cli demo-seguranca                  # senhas aleatórias, exibidas uma única vez
    python -m app.cli demo-seguranca --senha Demo2026seg

Idempotente: o que já existe é mantido (a senha só é definida para usuários novos). Empresas, pessoas e
e-mails (domínio reservado .example) são fictícios.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import Organization, Sensor, User
from .security import hash_secret, new_device_token, hash_token, new_password
from .services.audit import audit

EXPORTADORAS = [
    {"nome": "Exportadora Vale do Sol", "cidade": "Petrolina (PE)", "dominio": "valedosol.example",
     "sensor": ("VS-01", "Câmera Vale do Sol 01", "Talhão Syrah", "syrah")},
    {"nome": "Exportadora Rio Verde", "cidade": "Juazeiro (BA)", "dominio": "rioverde.example",
     "sensor": ("RV-01", "Câmera Rio Verde 01", "Talhão Chenin", "chenin-blanc")},
]
PERFIS = [("gestor", "Gestor(a)"), ("operador", "Operador(a)")]


def seed_security_demo(db: Session, password: str | None = None) -> list[dict]:
    """Cria o cenário e devolve os usuários criados (com a senha, exibida uma única vez)."""
    created: list[dict] = []
    for exp in EXPORTADORAS:
        org = db.query(Organization).filter(Organization.name == exp["nome"]).first()
        if not org:
            org = Organization(name=exp["nome"], city=exp["cidade"])
            db.add(org)
            db.flush()
        sid, sname, block, variety = exp["sensor"]
        if not db.get(Sensor, sid):
            db.add(Sensor(id=sid, name=sname, block=block, location="Demonstração de segurança", variety_id=variety, device="ESP32-CAM",
                          capture_interval_min=90, active=True, device_token_hash=hash_token(new_device_token()), organization_id=org.id))
        for role, label in PERFIS:
            email = f"{role}@{exp['dominio']}"
            if db.query(User.id).filter(User.email == email).first():
                continue
            pw = password or new_password()
            db.add(User(name=f"{label} {exp['nome'].replace('Exportadora ', '')}", email=email, role=role, password_hash=hash_secret(pw),
                        organization_id=org.id, is_active=True))
            created.append({"organizacao": org.name, "perfil": role, "email": email, "senha": pw})
    audit(db, "demo_seguranca", actor="cli", details={"usuarios_criados": len(created)})
    return created
