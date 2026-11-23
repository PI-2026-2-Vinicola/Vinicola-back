"""
Cenário da demonstração de segurança: duas exportadoras, um usuário de cada perfil em cada uma e um
cliente internacional por exportadora, com os dados sensíveis cifrados.

    python -m app.cli demo-seguranca                  # senhas aleatórias, exibidas uma única vez
    python -m app.cli demo-seguranca --senha Demo2026seg

Idempotente: o que já existe é mantido (a senha só é definida para usuários novos). Empresas, pessoas,
e-mails (domínio reservado .example) e telefones são fictícios.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import InternationalClient, Organization, Sensor, User
from .routers.clients import SENSITIVE, context
from .crypto import encrypt
from .security import hash_secret, new_device_token, hash_token, new_password
from .services.audit import audit

EXPORTADORAS = [
    {
        "nome": "Exportadora Vale do Sol", "cidade": "Petrolina (PE)", "dominio": "valedosol.example", "sensor": ("VS-01", "Câmera Vale do Sol 01", "Talhão Syrah", "syrah"),
        "cliente": {
            "company_name": "Nordic Fresh Imports AB", "country": "Suécia", "contact_name": "Erik Lindqvist",
            "email": "erik.lindqvist@nordicfresh.example", "phone": "+46 8 555 0142", "tax_id": "SE556677889901",
            "commercial_terms": "Contrato 2026/27: 40 t de Syrah e Cabernet Sauvignon, FOB Pecém, US$ 2,10/kg, pagamento em 30 dias.",
        },
    },
    {
        "nome": "Exportadora Rio Verde", "cidade": "Juazeiro (BA)", "dominio": "rioverde.example", "sensor": ("RV-01", "Câmera Rio Verde 01", "Talhão Chenin", "chenin-blanc"),
        "cliente": {
            "company_name": "Rotterdam Fruit Trading BV", "country": "Países Baixos", "contact_name": "Maarten de Vries",
            "email": "m.devries@rotterdamfruit.example", "phone": "+31 10 555 0199", "tax_id": "NL859123456B01",
            "commercial_terms": "Venda spot: 25 t de Chenin Blanc, CIF Roterdã, EUR 1,85/kg, carta de crédito.",
        },
    },
]
PERFIS = [("gestor", "Gestor(a)"), ("operador", "Operador(a)"), ("cliente", "Cliente")]


def seed_security_demo(db: Session, password: str | None = None) -> list[dict]:
    """Cria o cenário e devolve os usuários criados (com a senha, exibida uma única vez)."""
    created: list[dict] = []
    for exp in EXPORTADORAS:
        org = db.query(Organization).filter(Organization.name == exp["nome"]).first()
        if not org:
            org = Organization(name=exp["nome"], city=exp["cidade"])
            db.add(org)
            db.flush()
        data = exp["cliente"]
        client = db.query(InternationalClient).filter_by(organization_id=org.id, company_name=data["company_name"]).first()
        if not client:
            client = InternationalClient(organization_id=org.id, company_name=data["company_name"], country=data["country"])
            for field in SENSITIVE:
                setattr(client, f"{field}_enc", encrypt(data[field], context(org.id, field)))
            db.add(client)
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
                        organization_id=org.id, client_id=client.id if role == "cliente" else None, is_active=True))
            created.append({"organizacao": org.name, "perfil": role, "email": email, "senha": pw})
    audit(db, "demo_seguranca", actor="cli", details={"usuarios_criados": len(created)})
    return created
