"""
Comandos administrativos da OASIS.

    python -m app.cli create-user --name "Maria" --email maria@empresa.com --role gestor
    python -m app.cli reset-password --email maria@empresa.com
    python -m app.cli check-config
    python -m app.cli check-integrations         # testa ThingSpeak e Embrapa com chamadas reais
    python -m app.cli seed-demo [--days 30]     # dados SINTÉTICOS marcados como demonstração
    python -m app.cli clear-demo
    python -m app.cli export-dataset --out ml/datasets/oasis-campo   # fotos reais + pré-anotações para Label Studio/CVAT
    python -m app.cli baixar-fotos --out backups/fotos              # cópia local das fotos (disco ou bucket S3)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .config import get_settings
from .database import Base, SessionLocal, engine, ensure_columns, ensure_role_constraint
from .models import User
from .schemas import UserCreateIn, check_password
from .security import hash_secret
from .seed import bootstrap_admin, ensure_default_organization, seed_catalog
from .services.audit import audit


def _ask_password(given: str | None) -> str:
    password = given or getpass.getpass("Nova senha: ")
    if not given and password != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    try:
        return check_password(password)
    except ValueError as exc:
        sys.exit(str(exc))


def check_integrations() -> int:
    """
    Consulta de verdade as integrações configuradas e mostra o que cada serviço devolve
    (campos do canal do ThingSpeak, livro do RespondeAgro, culturas de uva do Agritec).
    Usa 1 requisição do Agritec, cuja cota gratuita é de 1.000. Devolve o número de falhas.
    """
    from .services.embrapa import EmbrapaService
    from .services.external import ExternalServiceError
    from .services.thingspeak import ThingSpeakService

    settings = get_settings()
    failures = 0

    print("ThingSpeak")
    if not settings.thingspeak_configured:
        print("  [--] não configurado: defina THINGSPEAK_CHANNEL_ID (e THINGSPEAK_READ_API_KEY se o canal for privado)")
    else:
        try:
            feed = ThingSpeakService(settings).feed("recent", 10)
            ch = feed.channel
            print(f"  [ok] canal {ch.id} - {ch.name or 'sem nome'}; último registro: {feed.last_entry_at or 'nenhum'}")
            for f in feed.fields:
                value = f.latest if f.numeric else f.latest_text
                print(f"       {f.key}: {f.name}{f' [{f.unit}]' if f.unit else ''} - último valor: {value if value is not None else 'sem dados'}")
            if not feed.fields:
                print("       nenhum campo nomeado nas configurações do canal")
            pos = feed.position
            if pos:
                print(f"       localização ({pos.source}): {pos.latitude}, {pos.longitude}{f' em {pos.at}' if pos.at else ''}")
            else:
                print("       localização: nenhuma (envie lat/long em cada registro ou campos Latitude/Longitude)")
        except ExternalServiceError as exc:
            failures += 1
            print(f"  [falha] {exc.message}")

    print("Embrapa AgroAPI")
    if not settings.embrapa_configured:
        print("  [--] não configurada: defina EMBRAPA_CONSUMER_KEY e EMBRAPA_CONSUMER_SECRET")
        return failures
    svc = EmbrapaService(settings)
    try:
        books = svc.books()
        book = next((b for b in books if b.id == settings.embrapa_book_id), None)
        print(f"  [ok] token obtido; RespondeAgro com {len(books)} livros indexados")
        if book:
            print(f"       livro '{book.id}': {book.questions} perguntas")
        else:
            failures += 1
            print(f"  [falha] livro '{settings.embrapa_book_id}' não está entre os indexados (EMBRAPA_BOOK_ID)")
    except ExternalServiceError as exc:
        failures += 1
        print(f"  [falha] RespondeAgro: {exc.message}")
    try:
        cultures = svc.grape_cultures()
        print(f"  [ok] Agritec v2: {len(cultures)} cultura(s) de uva" + ("" if cultures else " - a consulta do Zarc ficará sem dados"))
        for c in cultures:
            print(f"       {c.id}: {c.full_name or c.name}{' (com Zarc)' if c.has_zoning else ''}")
    except ExternalServiceError as exc:
        failures += 1
        print(f"  [falha] Agritec: {exc.message}")
    return failures


def download_photos(out: Path) -> int:
    """Baixa as fotos que ainda não estão na pasta (mesmo nome e tamanho); devolve o código de saída."""
    from .services.storage import StorageError, get_storage

    storage = get_storage()
    copied = skipped = 0
    try:
        for key, size in storage.list():
            dst = out / key
            if dst.is_file() and dst.stat().st_size == size:
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(storage.get(key))
            copied += 1
    except StorageError as exc:
        print(f"Falha ao acessar o armazenamento ({storage.name}): {exc}", file=sys.stderr)
        return 1
    print(f"Fotos em {out}: {copied} copiadas, {skipped} já existiam (armazenamento: {storage.name}).")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administração da OASIS")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create-user", help="Cria um usuário")
    c.add_argument("--name", required=True)
    c.add_argument("--email", required=True)
    c.add_argument("--role", choices=["admin", "gestor", "operador"], required=True)
    c.add_argument("--password")
    c.add_argument("--organizacao", type=int, help="id da organização (gestor e operador; vazio = organização padrão)")
    ds = sub.add_parser("demo-seguranca", help="Cria duas exportadoras com sensor, gestor e operador (demonstração do controle de acesso)")
    ds.add_argument("--senha", help="mesma senha para todos os usuários criados (padrão: aleatória e exibida uma vez)")
    sub.add_parser("matriz-rbac", help="Imprime a matriz de permissões (perfis × recursos) aplicada pela API")
    r = sub.add_parser("reset-password", help="Redefine a senha de um usuário (e o reativa)")
    r.add_argument("--email", required=True)
    r.add_argument("--password")
    b = sub.add_parser("baixar-fotos", help="Copia as fotos do armazenamento (disco ou bucket S3) para uma pasta local")
    b.add_argument("--out", type=Path, default=Path("backups/fotos"))
    sub.add_parser("check-config", help="Valida as configurações para produção")
    sub.add_parser("check-integrations", help="Testa as integrações com ThingSpeak e Embrapa (chamadas reais)")
    d = sub.add_parser("seed-demo", help="Gera dados SINTÉTICOS de demonstração (marcados como tal)")
    d.add_argument("--days", type=int, default=30)
    sub.add_parser("clear-demo", help="Remove os dados de demonstração")
    e = sub.add_parser("export-dataset", help="Exporta as fotos de campo com pré-anotações para revisão (Label Studio/CVAT)")
    e.add_argument("--out", type=Path, default=Path("ml/datasets/oasis-campo"))
    e.add_argument("--since", help="data inicial (AAAA-MM-DD)")
    e.add_argument("--sensor", help="somente um sensor")
    e.add_argument("--limit", type=int)
    e.add_argument("--image-prefix", default="/data/local-files/?d=", help="prefixo das imagens nas tarefas do Label Studio")
    args = parser.parse_args(argv)

    if args.command == "check-integrations":
        sys.exit(1 if check_integrations() else 0)
    if args.command == "matriz-rbac":
        from .rbac import matrix_markdown

        print(matrix_markdown())
        print("\nC = criar · R = consultar · U = atualizar · D = excluir · – = sem acesso")
        return
    if args.command == "baixar-fotos":
        sys.exit(download_photos(args.out))

    Base.metadata.create_all(engine)
    ensure_columns(engine)
    ensure_role_constraint(engine)
    with SessionLocal() as db:
        seed_catalog(db)
        ensure_default_organization(db)
        if args.command == "create-user":
            from fastapi import HTTPException

            from .routers.users import _membership

            try:
                data = UserCreateIn(name=args.name, email=args.email, role=args.role, password=_ask_password(args.password),
                                    organization_id=args.organizacao)
            except ValueError as exc:
                sys.exit(str(exc))
            if db.query(User).filter(User.email == data.email).first():
                sys.exit(f"Já existe um usuário com o e-mail {data.email}.")
            try:
                org_id = _membership(db, data.role, data.organization_id)
            except HTTPException as exc:
                sys.exit(str(exc.detail))
            db.add(User(name=data.name, email=data.email, role=data.role, password_hash=hash_secret(data.password), organization_id=org_id))
            audit(db, "usuario_criado", actor="cli", target=data.email, details={"perfil": data.role, "organizacao": org_id})
            print(f"Usuário {data.email} ({data.role}) criado.")
        elif args.command == "demo-seguranca":
            from .demo_seguranca import seed_security_demo

            if args.senha:
                try:
                    check_password(args.senha)
                except ValueError as exc:
                    sys.exit(str(exc))
            created = seed_security_demo(db, args.senha)
            if not created:
                print("Cenário já existente: nenhum usuário novo.")
            for u in created:
                print(f"{u['organizacao']:<26} {u['perfil']:<9} {u['email']:<32} senha: {u['senha']}")
            if created and not args.senha:
                print("Guarde as senhas: elas não serão exibidas novamente.")
        elif args.command == "reset-password":
            user = db.query(User).filter(User.email == args.email.strip().lower()).first()
            if not user:
                sys.exit("Usuário não encontrado.")
            user.password_hash = hash_secret(_ask_password(args.password))
            user.is_active = True
            audit(db, "senha_redefinida", actor="cli", target=user.email)
            print(f"Senha de {user.email} redefinida.")
        elif args.command == "check-config":
            settings = get_settings()
            problems = settings.validate_for_production()
            for w in settings.production_warnings():
                print(f"Atenção: {w}")
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
        elif args.command == "export-dataset":
            from datetime import date, datetime, time, timezone

            from .annotation_export import export_dataset

            since = None
            if args.since:
                try:
                    since = datetime.combine(date.fromisoformat(args.since), time.min, tzinfo=timezone.utc)
                except ValueError:
                    sys.exit("Use --since no formato AAAA-MM-DD.")
            result = export_dataset(db, args.out, since=since, sensor_id=args.sensor, limit=args.limit, image_prefix=args.image_prefix)
            print(f"{result['images']} imagens e {result['pre_annotations']} pré-anotações exportadas para {result['out']}")
            if result["missing_files"]:
                print(f"{result['missing_files']} leituras sem o arquivo de imagem no armazenamento foram ignoradas.")
            print("Revise as pré-anotações no Label Studio ou no CVAT antes de treinar (ml/README.md).")
        elif args.command == "clear-demo":
            from .demo import clear_demo

            readings, sensors = clear_demo(db)
            print(f"Removidas {readings} leituras de demonstração e {sensors} sensores DEMO.")


if __name__ == "__main__":
    main()
