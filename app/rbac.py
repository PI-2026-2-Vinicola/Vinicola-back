"""
Controle de acesso por perfil (RBAC) e isolamento por organização.

A matriz abaixo é a única fonte das permissões: as rotas da API a consultam (`require`), o endpoint
/auth/me a envia ao frontend (que só esconde o que a API já bloqueia) e `python -m app.cli matriz-rbac`
gera a tabela da documentação a partir dela.

Isolamento: o administrador é da plataforma e vê todas as organizações. Os demais perfis pertencem a uma
organização (exportadora) e só alcançam os dados dela. Toda negação fica registrada na auditoria como
"acesso_negado".
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .deps import client_ip, current_user_optional
from .models import User
from .services.audit import audit

ROLE_LABEL = {
    "admin": "Administrador",
    "gestor": "Exportadora (gestor)",
    "operador": "Operador de campo e logística",
}

ACTION_LABEL = {"C": "criar", "R": "consultar", "U": "atualizar", "D": "excluir"}

# recurso → (nome exibido, {perfil: operações}, {perfil: alcance})
MATRIX: dict[str, tuple[str, dict[str, str], dict[str, str]]] = {
    "usuarios": ("Usuários", {"admin": "CRUD"}, {}),
    "organizacoes": (
        "Organizações (exportadoras)",
        {"admin": "CRUD", "gestor": "R", "operador": "R"},
        {"gestor": "própria", "operador": "própria"},
    ),
    "sensores": (
        "Sensores IoT",
        {"admin": "CRUD", "gestor": "CRUD", "operador": "R"},
        {"gestor": "própria organização", "operador": "própria organização"},
    ),
    "leituras": (
        "Análises de qualidade (fotos, histórico e indicadores)",
        {"admin": "CRD", "gestor": "CRD", "operador": "CR"},
        {"gestor": "própria organização", "operador": "própria organização"},
    ),
    "medicoes": (
        "Dados climáticos (temperatura, umidade, luz, solo)",
        {"admin": "CR", "gestor": "CR", "operador": "CR"},
        {"gestor": "própria organização", "operador": "própria organização"},
    ),
    "importacoes": (
        "Importação de dados",
        {"admin": "CR", "gestor": "CR"},
        {"gestor": "própria organização"},
    ),
    "auditoria": ("Auditoria", {"admin": "R"}, {}),
    "sistema": ("Configurações e estado do sistema", {"admin": "R"}, {}),
}


def allowed(role: str | None, resource: str, action: str) -> bool:
    return bool(role) and action in MATRIX[resource][1].get(role, "")


def permissions_for(role: str) -> dict[str, str]:
    """Operações de cada recurso para um perfil (enviado ao frontend em /auth/me)."""
    return {resource: perms.get(role, "") for resource, (_, perms, _) in MATRIX.items()}


def matrix_markdown() -> str:
    roles = list(ROLE_LABEL)
    lines = ["| Recurso | " + " | ".join(ROLE_LABEL[r] for r in roles) + " |", "| --- |" + " --- |" * len(roles)]
    for _, (label, perms, scope) in MATRIX.items():
        cells = []
        for r in roles:
            ops = perms.get(r, "")
            cells.append(f"{ops} ({scope[r]})" if ops and r in scope else (ops or "–"))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def deny(db: Session, request: Request | None, user: User | None, message: str, details: dict) -> HTTPException:
    audit(db, "acesso_negado", actor=user.email if user else "anônimo", target=request.url.path if request else None,
          details=details, ip=client_ip(request) if request else None)
    return HTTPException(status.HTTP_403_FORBIDDEN, message)


@lru_cache
def require(resource: str, action: str, *, public_read: bool = False) -> Callable[..., User | None]:
    """
    Dependência das rotas: exige login e a operação na matriz. Com `public_read`, a consulta anônima é
    aceita só se PUBLIC_READ=true (demonstrações; a produção recusa essa configuração).
    """

    def checker(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user_optional)) -> User | None:
        if user is None:
            if public_read and action == "R" and get_settings().public_read:
                return None
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
        if not allowed(user.role, resource, action):
            label = MATRIX[resource][0].split(" (")[0].lower()
            raise deny(db, request, user, f"Acesso negado: o perfil {ROLE_LABEL[user.role]} não pode {ACTION_LABEL[action]} {label}.",
                        {"recurso": resource, "operacao": action, "perfil": user.role})
        return user

    return checker


# ------------------------------------------------------------------ isolamento por organização
NO_ORGANIZATION = -1  # usuário não administrador sem organização não alcança nenhum dado


def tenant(user: User | None) -> int | None:
    """Organização a que o usuário está restrito; None = sem restrição (administrador ou leitura pública)."""
    if user is None or user.role == "admin":
        return None
    return user.organization_id or NO_ORGANIZATION


def check_organization(db: Session, request: Request | None, user: User | None, organization_id: int | None, resource: str, target: str) -> None:
    """Nega (403 + auditoria) o acesso a um registro de outra organização."""
    scope = tenant(user)
    if scope is not None and organization_id != scope:
        raise deny(db, request, user, "Acesso negado: este registro pertence a outra organização.",
                    {"recurso": resource, "registro": target, "organizacao_do_registro": organization_id, "organizacao_do_usuario": scope})


def me_out(user: User):
    """Usuário com o rótulo do perfil e as permissões da matriz (para o frontend decidir o que mostrar)."""
    from .schemas import MeOut, UserOut

    return MeOut(**UserOut.model_validate(user).model_dump(), role_label=ROLE_LABEL[user.role], permissions=permissions_for(user.role))
