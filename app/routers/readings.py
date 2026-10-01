import csv
import io
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session, selectinload

from ..catalog import MATURATION_LABEL, QUALITY_LABEL
from ..database import get_db
from ..deps import client_ip, reader, require_roles
from ..models import Reading, Sensor, User, Variety
from ..schemas import ReadingOut, ReadingPage
from ..services.audit import audit
from ..services.readings import to_reading_out
from ..services.storage import image_file
from ..services.timeutil import from_db, farm_tz, period_bounds

router = APIRouter(prefix="/readings", tags=["Leituras"])
Sort = Literal["recentes", "antigas", "confianca_desc", "confianca_asc"]
EXPORT_LIMIT = 50_000


class ReadingFilters:
    """Filtros comuns da listagem e da exportação (todos opcionais e combináveis)."""

    def __init__(
        self,
        days: int | None = Query(default=None, ge=1, le=3660, description="Últimos N dias (horário local)"),
        date_from: date | None = Query(default=None, alias="dateFrom"),
        date_to: date | None = Query(default=None, alias="dateTo"),
        sensor_id: str | None = Query(default=None, alias="sensorId", max_length=20),
        variety_id: str | None = Query(default=None, alias="varietyId", max_length=40),
        quality: str | None = Query(default=None, max_length=40, description="Uma ou mais (boa,atencao,critica)"),
        classification: str | None = Query(default=None, max_length=40),
        maturation: str | None = Query(default=None, max_length=20),
        block: str | None = Query(default=None, max_length=40),
        source: str | None = Query(default=None, max_length=20),
        q: str | None = Query(default=None, max_length=80),
    ):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "A data inicial é posterior à data final.")
        self.days, self.date_from, self.date_to = days, date_from, date_to
        self.sensor_id, self.variety_id, self.classification = sensor_id, variety_id, classification
        self.maturation, self.block, self.source, self.q = maturation, block, source, q
        self.quality = [v for v in (quality or "").split(",") if v]
        if any(v not in QUALITY_LABEL for v in self.quality):
            raise HTTPException(422, "Qualidade inválida. Use boa, atencao ou critica.")

    def bounds(self):
        return period_bounds(self.days, self.date_from, self.date_to)

    def apply(self, db: Session, period: tuple | None = None) -> SAQuery:
        """Aplica os filtros. `period=(início, fim)` substitui o período informado (comparação com o período anterior)."""
        query = db.query(Reading).join(Sensor, Reading.sensor_id == Sensor.id)
        start, end = period or self.bounds()
        if start:
            query = query.filter(Reading.captured_at >= start)
        if self.date_to or period:
            query = query.filter(Reading.captured_at <= end)
        if self.sensor_id:
            query = query.filter(Reading.sensor_id == self.sensor_id.upper())
        if self.variety_id:
            query = query.filter(Reading.variety_id == self.variety_id)
        if self.quality:
            query = query.filter(Reading.quality.in_(self.quality))
        if self.classification:
            query = query.filter(Reading.classification == self.classification)
        if self.maturation:
            query = query.filter(Reading.maturation == self.maturation)
        if self.block:
            query = query.filter(Sensor.block == self.block)
        if self.source:
            query = query.filter(Reading.source == self.source)
        if self.q and self.q.strip():
            term = self.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{term}%"
            query = query.outerjoin(Variety, Reading.variety_id == Variety.id).filter(
                or_(
                    Reading.code.ilike(like, escape="\\"),
                    Reading.sensor_id.ilike(like, escape="\\"),
                    Sensor.name.ilike(like, escape="\\"),
                    Sensor.location.ilike(like, escape="\\"),
                    Variety.name.ilike(like, escape="\\"),
                    Reading.visual_condition.ilike(like, escape="\\"),
                    Reading.observations.ilike(like, escape="\\"),
                )
            )
        return query


ORDER = {
    "recentes": (Reading.captured_at.desc(), Reading.id.desc()),
    "antigas": (Reading.captured_at.asc(), Reading.id.asc()),
    "confianca_desc": (Reading.confidence.desc(), Reading.captured_at.desc()),
    "confianca_asc": (Reading.confidence.asc(), Reading.captured_at.desc()),
}


@router.get("", response_model=ReadingPage, response_model_by_alias=True)
def list_readings(
    filters: ReadingFilters = Depends(),
    sort: Sort = "recentes",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200, alias="pageSize"),
    db: Session = Depends(get_db),
    _: User | None = Depends(reader),
):
    """Histórico de leituras com filtros combináveis e paginação no servidor."""
    query = filters.apply(db)
    total = query.order_by(None).count()
    rows = (
        query.options(selectinload(Reading.detections), selectinload(Reading.sensor))
        .order_by(*ORDER[sort])
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ReadingPage(items=[to_reading_out(r) for r in rows], total=total, page=page, page_size=page_size)


def _safe(value) -> str:
    """Evita injeção de fórmulas ao abrir o CSV em planilhas."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export", response_class=StreamingResponse)
def export_readings(filters: ReadingFilters = Depends(), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Exporta as leituras filtradas em CSV (separador ';', UTF-8 com BOM para abrir no Excel)."""
    query = filters.apply(db)
    if query.order_by(None).count() > EXPORT_LIMIT:
        raise HTTPException(422, f"Mais de {EXPORT_LIMIT} leituras no filtro. Reduza o período.")
    varieties = dict(db.query(Variety.id, Variety.name).all())
    tz = farm_tz()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["codigo", "data_hora", "sensor", "bloco", "localizacao", "variedade", "qualidade", "classificacao", "confianca", "maturacao", "condicao_visual", "cachos", "origem", "modelo", "observacoes"])
    for r in query.options(selectinload(Reading.sensor)).order_by(*ORDER["recentes"]).yield_per(1000):
        writer.writerow(
            [
                _safe(v)
                for v in (
                    r.code,
                    from_db(r.captured_at).astimezone(tz).strftime("%d/%m/%Y %H:%M:%S"),
                    r.sensor_id,
                    r.sensor.block,
                    r.sensor.location,
                    varieties.get(r.variety_id, r.variety_id),
                    QUALITY_LABEL.get(r.quality, r.quality),
                    r.classification,
                    f"{r.confidence * 100:.1f}".replace(".", ","),
                    MATURATION_LABEL.get(r.maturation, r.maturation),
                    r.visual_condition,
                    r.clusters_detected,
                    r.source,
                    r.model_version,
                    r.observations,
                )
            ]
        )
    content = "﻿" + buffer.getvalue()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="oasis-leituras.csv"'},
    )


def get_reading_or_404(db: Session, code: str) -> Reading:
    reading = db.query(Reading).options(selectinload(Reading.detections), selectinload(Reading.sensor)).filter(Reading.code == code.upper()).first()
    if not reading:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Leitura {code} não encontrada.")
    return reading


@router.get("/{code}", response_model=ReadingOut, response_model_by_alias=True)
def get_reading(code: str, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    return to_reading_out(get_reading_or_404(db, code))


@router.get("/{code}/image", response_class=FileResponse)
def get_reading_image(code: str, size: Literal["full", "thumb"] = "full", db: Session = Depends(get_db), _: User | None = Depends(reader)):
    reading = get_reading_or_404(db, code)
    relative = reading.thumb_path if size == "thumb" and reading.thumb_path else reading.image_path
    if not relative:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta leitura não possui imagem armazenada.")
    try:
        path = image_file(relative)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Imagem não encontrada.")
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "O arquivo da imagem não está mais disponível no servidor.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reading(code: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_roles("admin"))):
    """Remove uma leitura (por exemplo, captura de teste) e seus arquivos de imagem."""
    reading = get_reading_or_404(db, code)
    files = [p for p in (reading.image_path, reading.thumb_path) if p]
    details = {"sensor": reading.sensor_id, "capturada_em": from_db(reading.captured_at).isoformat(), "origem": reading.source}
    db.delete(reading)
    audit(db, "leitura_excluida", actor=admin.email, target=reading.code, details=details, ip=client_ip(request))
    for p in files:
        try:
            image_file(p).unlink(missing_ok=True)
        except FileNotFoundError:
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)
