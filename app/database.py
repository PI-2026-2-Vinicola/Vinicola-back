import re
from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - configuração do driver
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")  # garante ON DELETE CASCADE
            cur.execute("PRAGMA journal_mode=WAL")  # leituras concorrentes durante escritas
            cur.close()

        return engine
    settings = get_settings()
    return create_engine(url, pool_pre_ping=True, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
                         pool_recycle=1800)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Colunas adicionadas depois da criação do esquema. `create_all` não altera tabelas existentes, então
# bancos já em uso (ex.: o oasis.db local) recebem as colunas aqui — sem apagar nenhum dado.
ADDED_COLUMNS = [
    ("readings", "predicted_variety", "VARCHAR(40)"),
    ("readings", "variety_confidence", "FLOAT"),
    # isolamento por organização (exportadora) e perfil cliente internacional
    ("users", "organization_id", "INTEGER"),
    ("users", "client_id", "INTEGER"),
    ("sensors", "organization_id", "INTEGER"),
    ("import_jobs", "organization_id", "INTEGER"),
]
ROLES = ("admin", "gestor", "operador", "cliente")


def ensure_columns(bind=None) -> list[str]:
    bind = bind or engine
    existing = {t: {c["name"] for c in inspect(bind).get_columns(t)} for t in {t for t, _, _ in ADDED_COLUMNS} if inspect(bind).has_table(t)}
    added = []
    with bind.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            if table in existing and column not in existing[table]:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                added.append(f"{table}.{column}")
    return added


def ensure_role_constraint(bind=None) -> bool:
    """
    Bancos criados antes do perfil "cliente" têm a restrição ck_users_role só com admin, gestor e operador.
    Troca a restrição pela lista atual (mais permissiva, então os dados existentes continuam válidos).
    Devolve True se alterou.
    """
    bind = bind or engine
    if not inspect(bind).has_table("users"):
        return False
    roles = ",".join(f"'{r}'" for r in ROLES)
    check = f"role IN ({roles})"
    dialect = bind.dialect.name
    with bind.begin() as conn:
        if dialect == "sqlite":
            sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")).scalar() or ""
            if "'cliente'" in sql or "ck_users_role" not in sql:
                return False
            # Procedimento da documentação do SQLite para afrouxar uma restrição CHECK sem recriar a tabela.
            new_sql = re.sub(r"role IN \([^)]*\)", check, sql)
            version = conn.execute(text("PRAGMA schema_version")).scalar()
            conn.execute(text("PRAGMA writable_schema=ON"))
            conn.execute(text("UPDATE sqlite_master SET sql=:sql WHERE type='table' AND name='users'"), {"sql": new_sql})
            conn.execute(text(f"PRAGMA schema_version={int(version) + 1}"))
            conn.execute(text("PRAGMA writable_schema=OFF"))
        elif dialect == "postgresql":
            definition = conn.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_users_role' AND conrelid='users'::regclass"
            )).scalar() or ""
            if "cliente" in definition:
                return False
            conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
            conn.execute(text(f"ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK ({check})"))
        elif dialect in ("mysql", "mariadb"):
            definition = conn.execute(text(
                "SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_NAME='ck_users_role'"
            )).scalar() or ""
            if "cliente" in definition:
                return False
            conn.execute(text("ALTER TABLE users DROP CHECK ck_users_role"))
            conn.execute(text(f"ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK ({check})"))
        else:
            return False
    if dialect == "sqlite":
        bind.dispose()  # conexões abertas guardam o esquema antigo
    return True
