"""
Comandos administrativos da OASIS.

    python -m app.cli create-user --name "Maria" --email maria@empresa.com --role gestor
    python -m app.cli reset-password --email maria@empresa.com
    python -m app.cli check-config
    python -m app.cli seed-demo [--days 30]     # dados SINTÉTICOS marcados como demonstração
    python -m app.cli clear-demo
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import User
from .schemas import UserCreateIn, check_password
from .security import hash_secret
from .seed import bootstrap_admin, seed_catalog
from .services.audit import audit


def _ask_password(given: str | None) -> str:
    password = given or getpass.getpass("Nova senha: ")
    if not given and password != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    try:
        return check_password(password)
    except ValueError as exc:
        sys.exit(str(exc))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administração da OASIS")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create-user", help="Cria um usuário")
    c.add_argument("--name", required=True)
    c.add_argument("--email", required=True)
    c.add_argument("--role", choices=["admin", "gestor", "operador"], required=True)
    c.add_argument("--password")
    r = sub.add_parser("reset-password", help="Redefine a senha de um usuário (e o reativa)")
    r.add_argument("--email", required=True)
    r.add_argument("--password")
    sub.add_parser("check-config", help="Valida as configurações para produção")
    d = sub.add_parser("seed-demo", help="Gera dados SINTÉTICOS de demonstração (marcados como tal)")
    d.add_argument("--days", type=int, default=30)
    sub.add_parser("clear-demo", help="Remove os dados de demonstração")
    args = parser.parse_args(argv)

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_catalog(db)
        if args.command == "create-user":
            try:
                data = UserCreateIn(name=args.name, email=args.email, role=args.role, password=_ask_password(args.password))
            except ValueError as exc:
                sys.exit(str(exc))
            if db.query(User).filter(User.email == data.email).first():
                sys.exit(f"Já existe um usuário com o e-mail {data.email}.")
            db.add(User(name=data.name, email=data.email, role=data.role, password_hash=hash_secret(data.password)))
            audit(db, "usuario_criado", actor="cli", target=data.email, details={"perfil": data.role})
            print(f"Usuário {data.email} ({data.role}) criado.")
        elif args.command == "reset-password":
            user = db.query(User).filter(User.email == args.email.strip().lower()).first()
            if not user:
                sys.exit("Usuário não encontrado.")
            user.password_hash = hash_secret(_ask_password(args.password))
            user.is_active = True
            audit(db, "senha_redefinida", actor="cli", target=user.email)
            print(f"Senha de {user.email} redefinida.")
        elif args.command == "check-config":
            problems = get_settings().validate_for_production()
            if problems:
                print("Configuração NÃO está pronta para produção:")
                for p in problems:
                    print(f"  - {p}")
                sys.exit(1)
            print("Configuração pronta para produção.")
        elif args.command == "seed-demo":
            from .demo import seed_demo

            bootstrap_admin(db)
            try:
                n = seed_demo(db, days=max(1, min(args.days, 180)))
            except RuntimeError as exc:
                sys.exit(str(exc))
            print(f"{n} leituras de DEMONSTRAÇÃO criadas nos sensores DEMO-01..03 (origem 'demonstracao').")
            print("Remova com: python -m app.cli clear-demo")
        elif args.command == "clear-demo":
            from .demo import clear_demo

            readings, sensors = clear_demo(db)
            print(f"Removidas {readings} leituras de demonstração e {sensors} sensores DEMO.")


if __name__ == "__main__":
    main()
