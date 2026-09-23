from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import reader
from ..models import Reading, Sensor, User, Variety
from ..schemas import ReadingOut
from ..services.readings import to_reading_out
from ..services.storage import image_file

router = APIRouter(prefix="/readings", tags=["Leituras"])


def filtered_query(
    db: Session,
    sensor_id: str | None = None,
    variety_id: str | None = None,
    quality: str | None = None,
    classification: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    days: int | None = None,
    q: str | None = None,
):
    query = db.query(Reading).join(Sensor).join(Variety, Reading.variety_id == Variety.id)
    if sensor_id:
        query = query.filter(Reading.sensor_id == sensor_id)
    if variety_id:
        query = query.filter(Reading.variety_id == variety_id)
    if quality:
        query = query.filter(Reading.quality == quality)
    if classification:
        query = query.filter(Reading.classification == classification)
    if days:
        query = query.filter(Reading.captured_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if date_from:
        query = query.filter(Reading.captured_at >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        query = query.filter(Reading.captured_at <= datetime.combine(date_to, time.max, timezone.utc))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(Reading.code.ilike(like), Reading.sensor_id.ilike(like), Sensor.location.ilike(like), Variety.name.ilike(like), Reading.observations.ilike(like), Reading.visual_condition.ilike(like))
        )
    return query


@router.get("", response_model=list[ReadingOut], response_model_by_alias=True)
def list_readings(
    response: Response,
    sensor_id: str | None = None,
    variety_id: str | None = None,
    quality: str | None = Query(default=None, pattern="^(boa|atencao|critica)$"),
    classification: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    days: int | None = Query(default=None, ge=1, le=366),
    q: str | None = None,
    limit: int = Query(default=200, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User | None = Depends(reader),
):
    """Histórico de leituras, mais recentes primeiro. O total filtrado vem no cabeçalho `X-Total-Count`."""
    query = filtered_query(db, sensor_id, variety_id, quality, classification, date_from, date_to, days, q)
    response.headers["X-Total-Count"] = str(query.count())
    rows = query.options(selectinload(Reading.detections), selectinload(Reading.sensor)).order_by(Reading.captured_at.desc()).offset(offset).limit(limit).all()
    return [to_reading_out(r) for r in rows]


def get_reading_or_404(db: Session, code: str) -> Reading:
    reading = db.query(Reading).filter(Reading.code == code).first()
    if not reading:
        raise HTTPException(404, f"Leitura {code} não encontrada.")
    return reading


@router.get("/{code}", response_model=ReadingOut, response_model_by_alias=True)
def get_reading(code: str, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    return to_reading_out(get_reading_or_404(db, code))


@router.get("/{code}/image", response_class=FileResponse)
def get_reading_image(code: str, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    reading = get_reading_or_404(db, code)
    if not reading.image_path or not image_file(reading.image_path).exists():
        raise HTTPException(404, "Esta leitura não possui imagem armazenada.")
    return FileResponse(image_file(reading.image_path), media_type="image/jpeg")
