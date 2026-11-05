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
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


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
]


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
