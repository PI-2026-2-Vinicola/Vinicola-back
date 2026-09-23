from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import reader
from ..models import Reading, Sensor, User, Variety
from ..schemas import DayBucketOut, QualityCount, SummaryOut, VarietyOut

router = APIRouter(tags=["Indicadores"])


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@router.get("/stats/summary", response_model=SummaryOut, response_model_by_alias=True)
def summary(days: int = Query(default=7, ge=1, le=366), variety_id: str | None = None, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Indicadores do dashboard: sensores ativos, leituras, uvas analisadas, qualidade geral e alertas."""
    base = db.query(Reading).filter(Reading.captured_at >= _since(days))
    if variety_id:
        base = base.filter(Reading.variety_id == variety_id)
    counts = dict(base.with_entities(Reading.quality, func.count()).group_by(Reading.quality).all())
    q = QualityCount(boa=counts.get("boa", 0), atencao=counts.get("atencao", 0), critica=counts.get("critica", 0))
    q.total = q.boa + q.atencao + q.critica
    clusters, avg_conf = base.with_entities(func.coalesce(func.sum(Reading.clusters_detected), 0), func.coalesce(func.avg(Reading.confidence), 0)).one()
    sensors_total = db.query(Sensor).count()
    sensors_active = db.query(Sensor).filter(Sensor.status != "offline").count()
    return SummaryOut(
        days=days, sensors_total=sensors_total, sensors_active=sensors_active, readings=q.total, clusters=int(clusters),
        quality=q, quality_ratio=round(q.boa / q.total, 4) if q.total else 0.0, avg_confidence=round(float(avg_conf), 4), alerts=q.atencao + q.critica,
    )


@router.get("/stats/by-day", response_model=list[DayBucketOut], response_model_by_alias=True)
def by_day(days: int = Query(default=30, ge=1, le=366), variety_id: str | None = None, sensor_id: str | None = None, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Leituras por dia e classificação (UTC) — base dos gráficos de evolução."""
    query = db.query(Reading).filter(Reading.captured_at >= _since(days))
    if variety_id:
        query = query.filter(Reading.variety_id == variety_id)
    if sensor_id:
        query = query.filter(Reading.sensor_id == sensor_id)
    buckets: dict = {}
    for r in query.with_entities(Reading.captured_at, Reading.quality, Reading.confidence):
        day = r.captured_at.date()
        b = buckets.setdefault(day, {"boa": 0, "atencao": 0, "critica": 0, "conf": 0.0})
        b[r.quality] += 1
        b["conf"] += r.confidence
    out = []
    for day in sorted(buckets):
        b = buckets[day]
        total = b["boa"] + b["atencao"] + b["critica"]
        out.append(DayBucketOut(day=day, boa=b["boa"], atencao=b["atencao"], critica=b["critica"], total=total, avg_confidence=round(b["conf"] / total, 4)))
    return out


@router.get("/stats/by-variety")
def by_variety(days: int = Query(default=30, ge=1, le=366), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Histórico por variedade: total, distribuição e confiança média."""
    rows = (
        db.query(Reading.variety_id, Reading.quality, func.count(), func.avg(Reading.confidence))
        .filter(Reading.captured_at >= _since(days))
        .group_by(Reading.variety_id, Reading.quality)
        .all()
    )
    out: dict[str, dict] = {}
    for vid, quality, n, avg in rows:
        v = out.setdefault(vid, {"varietyId": vid, "total": 0, "boa": 0, "atencao": 0, "critica": 0, "_conf": 0.0})
        v[quality] = n
        v["total"] += n
        v["_conf"] += float(avg) * n
    for v in out.values():
        v["avgConfidence"] = round(v.pop("_conf") / v["total"], 4) if v["total"] else 0
    return sorted(out.values(), key=lambda v: -v["total"])


@router.get("/varieties", response_model=list[VarietyOut], response_model_by_alias=True, tags=["Variedades"])
def varieties(db: Session = Depends(get_db)):
    return db.query(Variety).order_by(Variety.name).all()
