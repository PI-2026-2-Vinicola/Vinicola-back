"""Aplicação FastAPI da OSAIS."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .database import Base, SessionLocal, engine
from .routers import auth, ingest, readings, sensors, stats
from .seed import seed_catalog, seed_demo
from .services.detector import get_detector


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if get_settings().seed_demo:
            created = seed_demo(db)
            if created:
                print(f"[OSAIS] Banco populado com {created} leituras de demonstração.")
        else:
            seed_catalog(db)
    yield


app = FastAPI(
    title="OSAIS API",
    version=__version__,
    description=(
        "API da OSAIS — Observação Agroambiental Sensorizada, Inteligente e Sustentável. "
        "Recebe imagens dos sensores IoT (ESP32 + câmera), executa o modelo YOLO, classifica as uvas "
        "e disponibiliza sensores, leituras e indicadores para o dashboard."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

API_PREFIX = "/api/v1"
for r in (auth.router, sensors.router, readings.router, ingest.router, stats.router):
    app.include_router(r, prefix=API_PREFIX)


@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok", "version": __version__, "detector": get_detector().name}
