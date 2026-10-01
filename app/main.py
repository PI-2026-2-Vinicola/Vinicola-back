"""Aplicação FastAPI da OASIS."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from . import __version__
from .config import DEFAULT_JWT_SECRET, get_settings
from .database import Base, SessionLocal, engine
from .routers import auth, imports, ingest, readings, sensors, stats, system, users
from .seed import bootstrap_admin, seed_catalog
from .services.detector import get_detector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oasis")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    problems = settings.validate_for_production()
    if settings.is_production and problems:
        raise RuntimeError("Configuração insegura para produção: " + " ".join(problems))
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        log.warning("JWT_SECRET padrão em uso — aceitável só em desenvolvimento. Defina JWT_SECRET no .env.")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        try:
            seed_catalog(db)
            bootstrap_admin(db)
        except IntegrityError:
            # Outro processo (ex.: recarregador do uvicorn) inicializou o banco ao mesmo tempo.
            db.rollback()
    detector = get_detector()
    log.info("OASIS API %s pronta — detector: %s (%s)", __version__, detector.name, getattr(detector, "version", ""))
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


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro interno no servidor. A equipe técnica pode consultar os logs da API."})


API_PREFIX = "/api/v1"
for r in (auth.router, users.router, sensors.router, readings.router, ingest.router, stats.router, imports.router, system.router):
    app.include_router(r, prefix=API_PREFIX)


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok", "version": __version__}
