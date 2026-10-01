"""Indicadores calculados no banco a partir das leituras reais (nenhum valor é estimado no frontend)."""

from collections import defaultdict
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import reader
from ..models import EnvironmentReading, Reading, Sensor, User
from ..schemas import BucketOut, EnvironmentDay, EnvironmentSummary, GroupOut, PeriodTotals, QualityCount, SummaryOut
from ..services.sensors import status_counts
from ..services.timeutil import farm_tz, from_db, local_date, local_hour
from .readings import ReadingFilters

router = APIRouter(prefix="/stats", tags=["Indicadores"])
DEFAULT_DAYS = 30
MAX_FILLED_DAYS = 400


def _scoped(filters: ReadingFilters) -> ReadingFilters:
    if not filters.days and not filters.date_from:
        filters.days = DEFAULT_DAYS
    return filters


def _quality_counts(query) -> QualityCount:
    counts = dict(query.order_by(None).with_entities(Reading.quality, func.count(Reading.id)).group_by(Reading.quality).all())
    q = QualityCount(boa=counts.get("boa", 0), atencao=counts.get("atencao", 0), critica=counts.get("critica", 0))
    q.total = q.boa + q.atencao + q.critica
    return q


def _sensors_in_scope(db: Session, f: ReadingFilters) -> list[Sensor]:
    q = db.query(Sensor)
    if f.sensor_id:
        q = q.filter(Sensor.id == f.sensor_id.upper())
    if f.block:
        q = q.filter(Sensor.block == f.block)
    if f.variety_id:
        q = q.filter(Sensor.variety_id == f.variety_id)
    return q.all()


def _environment_query(db: Session, f: ReadingFilters, start, end):
    q = db.query(EnvironmentReading).join(Sensor, EnvironmentReading.sensor_id == Sensor.id).filter(EnvironmentReading.measured_at <= end)
    if start:
        q = q.filter(EnvironmentReading.measured_at >= start)
    if f.sensor_id:
        q = q.filter(EnvironmentReading.sensor_id == f.sensor_id.upper())
    if f.block:
        q = q.filter(Sensor.block == f.block)
    if f.variety_id:
        q = q.filter(Sensor.variety_id == f.variety_id)
    return q


@router.get("/summary", response_model=SummaryOut, response_model_by_alias=True)
def summary(filters: ReadingFilters = Depends(), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Indicadores do dashboard no período, com comparação ao período anterior de mesma duração."""
    f = _scoped(filters)
    start, end = f.bounds()
    base = f.apply(db)
    quality = _quality_counts(base)
    clusters, avg_conf, last = base.order_by(None).with_entities(
        func.coalesce(func.sum(Reading.clusters_detected), 0), func.avg(Reading.confidence), func.max(Reading.captured_at)
    ).one()

    previous = None
    if start:
        prev = _quality_counts(f.apply(db, period=(start - (end - start), start - timedelta(microseconds=1))))
        previous = PeriodTotals(readings=prev.total, quality_ratio=round(prev.boa / prev.total, 4) if prev.total else None)

    sensors = status_counts(_sensors_in_scope(db, f))
    env = _environment_query(db, f, start, end)
    t_avg, h_avg, n_env, last_env = env.order_by(None).with_entities(
        func.avg(EnvironmentReading.temperature_c), func.avg(EnvironmentReading.humidity_pct), func.count(EnvironmentReading.id), func.max(EnvironmentReading.measured_at)
    ).one()

    return SummaryOut(
        period_start=start or from_db(db.query(func.min(Reading.captured_at)).scalar()) or end,
        period_end=end,
        sensors_total=sum(sensors.values()),
        sensors_online=sensors["online"],
        sensors_attention=sensors["atencao"],
        sensors_offline=sensors["offline"],
        readings=quality.total,
        clusters=int(clusters or 0),
        quality=quality,
        quality_ratio=round(quality.boa / quality.total, 4) if quality.total else None,
        avg_confidence=round(float(avg_conf), 4) if avg_conf is not None else None,
        alerts=quality.atencao + quality.critica,
        last_reading_at=from_db(last),
        previous=previous,
        environment=EnvironmentSummary(
            avg_temperature_c=round(float(t_avg), 1) if t_avg is not None else None,
            avg_humidity_pct=round(float(h_avg), 1) if h_avg is not None else None,
            measurements=int(n_env or 0),
            last_measured_at=from_db(last_env),
        ),
    )


def _bucket(key: str, label: str, values: dict) -> BucketOut:
    total = values["boa"] + values["atencao"] + values["critica"]
    return BucketOut(
        key=key, label=label, boa=values["boa"], atencao=values["atencao"], critica=values["critica"], total=total,
        avg_confidence=round(values["conf"] / total, 4) if total else None,
    )


@router.get("/by-day", response_model=list[BucketOut], response_model_by_alias=True)
def by_day(filters: ReadingFilters = Depends(), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Leituras por dia (fuso da propriedade) e qualidade. Dias sem leitura aparecem com zero."""
    f = _scoped(filters)
    start, end = f.bounds()
    buckets: dict = defaultdict(lambda: {"boa": 0, "atencao": 0, "critica": 0, "conf": 0.0})
    for captured_at, quality, confidence in f.apply(db).order_by(None).with_entities(Reading.captured_at, Reading.quality, Reading.confidence):
        b = buckets[local_date(captured_at)]
        b[quality] += 1
        b["conf"] += confidence
    if start:
        first, last = start.astimezone(farm_tz()).date(), end.astimezone(farm_tz()).date()
        if (last - first).days <= MAX_FILLED_DAYS:
            day = first
            while day <= last:
                buckets[day]  # cria o dia vazio
                day += timedelta(days=1)
    return [_bucket(d.isoformat(), d.strftime("%d/%m"), buckets[d]) for d in sorted(buckets)]


@router.get("/by-hour", response_model=list[BucketOut], response_model_by_alias=True)
def by_hour(filters: ReadingFilters = Depends(), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Distribuição das leituras por hora do dia (horário local)."""
    f = _scoped(filters)
    buckets = {h: {"boa": 0, "atencao": 0, "critica": 0, "conf": 0.0} for h in range(24)}
    for captured_at, quality, confidence in f.apply(db).order_by(None).with_entities(Reading.captured_at, Reading.quality, Reading.confidence):
        b = buckets[local_hour(captured_at)]
        b[quality] += 1
        b["conf"] += confidence
    return [_bucket(str(h), f"{h:02d}h", buckets[h]) for h in range(24)]


GROUPS = {
    "sensor": Reading.sensor_id,
    "variety": Reading.variety_id,
    "maturation": Reading.maturation,
    "classification": Reading.classification,
    "source": Reading.source,
    "block": Sensor.block,
}


@router.get("/breakdown", response_model=list[GroupOut], response_model_by_alias=True)
def breakdown(
    by: Literal["sensor", "variety", "maturation", "classification", "source", "block"] = Query(...),
    filters: ReadingFilters = Depends(),
    db: Session = Depends(get_db),
    _: User | None = Depends(reader),
):
    """Totais por sensor, variedade, estágio de maturação, classificação, origem ou bloco."""
    f = _scoped(filters)
    column = GROUPS[by]
    rows = f.apply(db).order_by(None).with_entities(column, Reading.quality, func.count(Reading.id), func.sum(Reading.confidence)).group_by(column, Reading.quality).all()
    out: dict[str, dict] = {}
    for key, quality, n, conf in rows:
        g = out.setdefault(key, {"boa": 0, "atencao": 0, "critica": 0, "total": 0, "conf": 0.0})
        g[quality] += n
        g["total"] += n
        g["conf"] += float(conf or 0)
    return sorted(
        (GroupOut(key=k, total=g["total"], boa=g["boa"], atencao=g["atencao"], critica=g["critica"], avg_confidence=round(g["conf"] / g["total"], 4) if g["total"] else None) for k, g in out.items()),
        key=lambda g: (-g.total, g.key),
    )


@router.get("/environment", response_model=list[EnvironmentDay], response_model_by_alias=True)
def environment_by_day(filters: ReadingFilters = Depends(), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    """Médias diárias das medições ambientais (temperatura, umidade, luminosidade, umidade do solo)."""
    f = _scoped(filters)
    start, end = f.bounds()
    days: dict = defaultdict(lambda: defaultdict(list))
    rows = _environment_query(db, f, start, end).with_entities(
        EnvironmentReading.measured_at, EnvironmentReading.temperature_c, EnvironmentReading.humidity_pct, EnvironmentReading.luminosity_lux, EnvironmentReading.soil_moisture_pct
    )
    for at, t, h, lux, soil in rows:
        d = days[local_date(at)]
        d["n"].append(1)
        for name, v in (("t", t), ("h", h), ("lux", lux), ("soil", soil)):
            if v is not None:
                d[name].append(v)

    def avg(values, digits=1):
        return round(sum(values) / len(values), digits) if values else None

    return [
        EnvironmentDay(
            key=day.isoformat(), label=day.strftime("%d/%m"), measurements=len(d["n"]),
            avg_temperature_c=avg(d["t"]), min_temperature_c=min(d["t"]) if d["t"] else None, max_temperature_c=max(d["t"]) if d["t"] else None,
            avg_humidity_pct=avg(d["h"]), avg_luminosity_lux=avg(d["lux"], 0), avg_soil_moisture_pct=avg(d["soil"]),
        )
        for day, d in sorted(days.items())
    ]

