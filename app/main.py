"""Aplicação FastAPI da OASIS."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import __version__
from .config import DEFAULT_JWT_SECRET, get_settings
from .database import Base, SessionLocal, engine, ensure_columns, ensure_role_constraint
from .routers import auth, imports, ingest, integrations, organizations, readings, sensors, stats, system, users
from .seed import bootstrap_admin, ensure_default_organization, seed_catalog
from .services.detector import get_detector
from .services.external import ExternalServiceError
from .services.storage import StorageError, get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# O httpx registra a URL completa de cada requisição em INFO; a Read API Key do ThingSpeak vai na URL.
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("oasis")
settings = get_settings()
# Commit em produção: o Render define RENDER_GIT_COMMIT; outras hospedagens podem usar OASIS_COMMIT.
DEPLOYED_COMMIT = os.getenv("RENDER_GIT_COMMIT") or os.getenv("OASIS_COMMIT") or ""


def _check_storage() -> None:
    """Confere o destino das fotos na inicialização (só avisa: a API sobe mesmo assim)."""
    if settings.storage_backend != "s3":
        log.info("Imagens: disco local (%s)", settings.storage_dir)
        return
    try:
        next(iter(get_storage().list("__verificacao__/")), None)
        log.info("Imagens: bucket S3 %s em %s", settings.s3_bucket, settings.s3_endpoint_url)
    except StorageError as exc:
        log.error("Imagens: bucket S3 %s inacessível (%s). Confira S3_* e se o bucket existe.", settings.s3_bucket, exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    problems = settings.validate_for_production()
    if settings.is_production and problems:
        raise RuntimeError("Configuração insegura para produção: " + " ".join(problems))
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        log.warning("JWT_SECRET padrão em uso — aceitável só em desenvolvimento. Defina JWT_SECRET no .env.")
    if settings.is_production:
        for warning in settings.production_warnings():
            log.warning("Produção: %s", warning)
    Base.metadata.create_all(engine)
    added = ensure_columns(engine)
    if added:
        log.info("Banco atualizado: colunas adicionadas %s", ", ".join(added))
    if ensure_role_constraint(engine):
        log.info("Banco atualizado: perfil cliente aceito na tabela de usuários")
    with SessionLocal() as db:
        try:
            seed_catalog(db)
            ensure_default_organization(db)
            bootstrap_admin(db)
        except IntegrityError:
            # Outro processo (ex.: recarregador do uvicorn) inicializou o banco ao mesmo tempo.
            db.rollback()
    detector = get_detector()
    _check_storage()
    log.info("OASIS API %s pronta — detector: %s (%s)", __version__, detector.name, getattr(detector, "version", ""))
    log.info(
        "Integrações — ThingSpeak: %s · Embrapa AgroAPI: %s",
        "configurado" if settings.thingspeak_configured else "não configurado",
        "configurada" if settings.embrapa_configured else "não configurada",
    )
    yield


app = FastAPI(
    title="OASIS API",
    version=__version__,
    description=(
        "API da OASIS — Observação Agroambiental Sensorizada, Inteligente e Sustentável. "
        "Recebe imagens dos sensores IoT, analisa e classifica os cachos, importa dados históricos "
        "e disponibiliza sensores, leituras, medições ambientais e indicadores."
    ),
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Token"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/api/") or request.url.path == "/health":
        # A API só devolve JSON, imagens e arquivos: nada pode ser executado nem embutido em outra página.
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    behind_https_proxy = settings.is_production and request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    if request.url.scheme == "https" or behind_https_proxy:
        # Atrás de proxy o esquema vem de X-Forwarded-Proto (uvicorn --proxy-headers no Caddy; Render termina o TLS).
        # Navegadores ignoram HSTS recebido por HTTP, então o cabeçalho não tem efeito se for forjado sem TLS.
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


FIELD_LABEL = {
    "email": "e-mail", "password": "senha", "newPassword": "nova senha", "currentPassword": "senha atual", "name": "nome",
    "role": "perfil", "id": "código", "block": "bloco", "location": "localização", "lat": "latitude", "lng": "longitude",
    "varietyId": "variedade", "captureIntervalMin": "intervalo de captura", "sensor_id": "sensor", "image": "imagem",
    "file": "arquivo", "kind": "tipo", "captured_at": "data de captura",
}


def _friendly(error: dict) -> str:
    loc = [str(p) for p in error.get("loc", []) if p not in ("body", "query", "path", "form")]
    field = FIELD_LABEL.get(loc[-1], loc[-1]) if loc else ""
    kind, ctx = error.get("type", ""), error.get("ctx") or {}
    if kind == "missing":
        msg = "campo obrigatório"
    elif kind == "value_error":
        msg = str(ctx.get("error") or error.get("msg", "")).removeprefix("Value error, ")
    elif kind == "string_too_short":
        msg = f"deve ter pelo menos {ctx.get('min_length')} caracteres"
    elif kind == "string_too_long":
        msg = f"deve ter no máximo {ctx.get('max_length')} caracteres"
    elif kind in ("greater_than_equal", "greater_than"):
        msg = f"deve ser maior ou igual a {ctx.get('ge', ctx.get('gt'))}"
    elif kind in ("less_than_equal", "less_than"):
        msg = f"deve ser menor ou igual a {ctx.get('le', ctx.get('lt'))}"
    elif kind == "literal_error":
        msg = f"valor inválido (use {ctx.get('expected')})"
    elif kind.startswith(("int_", "float_")):
        msg = "deve ser um número"
    elif kind.startswith(("date", "datetime")):
        msg = "data inválida"
    elif kind == "string_pattern_mismatch":
        msg = "formato inválido"
    else:
        msg = error.get("msg", "valor inválido")
    return f"{field.capitalize()}: {msg}" if field else msg[:1].upper() + msg[1:]


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    errors = [_friendly(e) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors[0] if errors else "Dados inválidos.", "errors": errors})


@app.exception_handler(ExternalServiceError)
async def external_service_handler(_: Request, exc: ExternalServiceError):
    """Falhas do ThingSpeak/AgroAPI chegam ao cliente com status próprio e mensagem em português."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno no servidor. A equipe técnica pode consultar os logs da API."})


API_PREFIX = "/api/v1"
for r in (auth.router, users.router, organizations.router, sensors.router, readings.router, ingest.router, stats.router, imports.router, system.router, integrations.router):
    app.include_router(r, prefix=API_PREFIX)


@app.get("/health", tags=["Sistema"])
def health():
    """Saúde da API para o healthcheck do Docker e monitores externos: responde 503 se o banco não aceitar consultas."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — qualquer falha de conexão deixa a API indisponível
        log.warning("Healthcheck: banco de dados indisponível (%s)", exc.__class__.__name__)
        return JSONResponse(status_code=503, content={"status": "indisponivel", "database": "erro", "version": __version__, "commit": DEPLOYED_COMMIT or None})
    body = {"status": "ok", "database": "ok", "version": __version__}
    if DEPLOYED_COMMIT:
        body["commit"] = DEPLOYED_COMMIT  # o pipeline confere que o deploy publicou o commit enviado
    return body
