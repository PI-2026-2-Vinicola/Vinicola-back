"""
Integrações externas: ThingSpeak (dados de IoT) e Embrapa AgroAPI (RespondeAgro e Agritec).

- Os dados do ThingSpeak exigem login, como as demais leituras de sensores (`reader`).
- O conteúdo da Embrapa é público (aba Uvas), com limite de consultas por IP e cache no servidor,
  para não esgotar a cota do plano da AgroAPI. Nenhuma credencial chega ao navegador.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from ..catalog import VALE_MUNICIPALITIES
from ..config import get_settings
from ..deps import client_ip, reader
from ..models import User
from ..schemas import (
    AgritecCultureOut,
    AgritecZarcOut,
    EmbrapaBookOut,
    EmbrapaQuestionOut,
    EmbrapaSearchOut,
    IntegrationsStatus,
    IntegrationState,
    MunicipalityOut,
    ThingSpeakFeedOut,
)
from ..services.detector import YoloDetector, get_detector
from ..services.embrapa import EmbrapaService, get_embrapa
from ..services.external import RateLimiter
from ..services.thingspeak import ThingSpeakService, get_thingspeak

router = APIRouter(tags=["Integrações"])
_embrapa_limiter = RateLimiter(get_settings().embrapa_rate_limit_per_minute, 60)


def embrapa_rate_limit(request: Request) -> None:
    if not _embrapa_limiter.hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Muitas consultas em pouco tempo. Aguarde um minuto e tente novamente.")


def reset_embrapa_rate_limit() -> None:
    _embrapa_limiter.reset()


limited = [Depends(embrapa_rate_limit)]


@router.get("/public/integrations", response_model=IntegrationsStatus, response_model_by_alias=True)
def integrations_status(ts: ThingSpeakService = Depends(get_thingspeak), em: EmbrapaService = Depends(get_embrapa)):
    """Situação das integrações (sem identificadores nem credenciais), usada pelas páginas públicas."""
    yolo = isinstance(get_detector(), YoloDetector)
    detector = IntegrationState(
        configured=True,
        detail="Modelo YOLO treinado em uso." if yolo else "Análise de cor em uso; nenhum modelo YOLO treinado foi carregado.",
    )
    return IntegrationsStatus(thingspeak=ts.status(), embrapa=em.status(), detector=detector, yolo_loaded=yolo)


@router.get("/iot/thingspeak", response_model=ThingSpeakFeedOut, response_model_by_alias=True)
def thingspeak_feed(
    period: Literal["recent", "24h", "7d", "30d"] = "recent",
    results: int = Query(default=100, ge=10, le=1000, description="Registros no período `recent`"),
    _: User | None = Depends(reader),
    ts: ThingSpeakService = Depends(get_thingspeak),
):
    """Registros do canal configurado no ThingSpeak: últimos N ou amostragem de 24 h, 7 ou 30 dias."""
    return ts.feed(period, results)


@router.get("/embrapa/questions", response_model=EmbrapaSearchOut, response_model_by_alias=True, dependencies=limited)
def embrapa_questions(
    q: str = Query(..., min_length=2, max_length=120, description="Texto de busca"),
    page: int = Query(default=1, ge=1, le=50),
    page_size: int = Query(default=8, ge=1, le=20, alias="pageSize"),
    scope: Literal["grape", "all"] = Query(default="grape", description="grape = livro sobre uva; all = todos os livros"),
    em: EmbrapaService = Depends(get_embrapa),
):
    """Perguntas e respostas da Coleção 500 Perguntas 500 Respostas (RespondeAgro)."""
    if len(q.strip()) < 2:
        raise HTTPException(422, "Digite pelo menos 2 caracteres para buscar.")
    return em.search(q, page, page_size, scope)


@router.get("/embrapa/questions/{doc_id}", response_model=EmbrapaQuestionOut, response_model_by_alias=True, dependencies=limited)
def embrapa_question(doc_id: str = Path(max_length=48), em: EmbrapaService = Depends(get_embrapa)):
    item = em.question(doc_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pergunta não encontrada no RespondeAgro.")
    return item


@router.get("/embrapa/books", response_model=list[EmbrapaBookOut], response_model_by_alias=True, dependencies=limited)
def embrapa_books(em: EmbrapaService = Depends(get_embrapa)):
    """Livros indexados no RespondeAgro e o número de perguntas de cada um."""
    return em.books()


@router.get("/embrapa/zarc/cultures", response_model=list[AgritecCultureOut], response_model_by_alias=True, dependencies=limited)
def zarc_cultures(em: EmbrapaService = Depends(get_embrapa)):
    """Culturas de uva disponíveis no Agritec (Zarc)."""
    return em.grape_cultures()


@router.get("/embrapa/zarc", response_model=AgritecZarcOut, response_model_by_alias=True, dependencies=limited)
def zarc(
    culture_id: int = Query(..., ge=1, alias="cultureId"),
    ibge: int = Query(..., ge=1_000_000, le=9_999_999, description="Código IBGE do município"),
    em: EmbrapaService = Depends(get_embrapa),
):
    """Janelas de plantio do Zarc para uma cultura de uva em um município do Submédio São Francisco."""
    return em.zarc(culture_id, ibge)


@router.get("/embrapa/municipalities", response_model=list[MunicipalityOut], response_model_by_alias=True)
def zarc_municipalities():
    """Municípios oferecidos na consulta do Zarc (lista local, sem chamada externa)."""
    return [MunicipalityOut(ibge_code=m["ibge"], name=m["name"], uf=m["uf"]) for m in VALE_MUNICIPALITIES]
