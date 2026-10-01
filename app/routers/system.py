"""Catálogo, visão pública agregada, estado do sistema e trilha de auditoria."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..database import engine, get_db
from ..models import AuditLog, EnvironmentReading, ImportJob, Reading, Sensor, User, Variety
from ..schemas import AuditOut, AuditPage, PublicOverview, SystemStatus, VarietyOut
from ..services.detector import YoloDetector, get_detector
from ..services.sensors import status_counts
from ..services.storage import storage_size_mb
from ..services.timeutil import from_db, period_bounds
from ..deps import require_roles

router = APIRouter(tags=["Sistema"])
admin_only = require_roles("admin")


@router.get("/varieties", response_model=list[VarietyOut], response_model_by_alias=True)
def varieties(db: Session = Depends(get_db)):
    """Catálogo de variedades (dado de referência, público)."""
    return db.query(Variety).order_by(Variety.name).all()


@router.get("/public/overview", response_model=PublicOverview, response_model_by_alias=True)
def public_overview(db: Session = Depends(get_db)):
    """Números agregados exibidos na página inicial. Não expõe leituras, imagens nem localização."""
    start, _end = period_bounds(30, None, None)
    sensors = db.query(Sensor).all()
    counts = status_counts(sensors)
    recent = db.query(Reading).filter(Reading.captured_at >= start, Reading.source != "demonstracao")
    total, avg_conf, varieties_n = recent.with_entities(func.count(Reading.id), func.avg(Reading.confidence), func.count(func.distinct(Reading.variety_id))).one()
    good = recent.filter(Reading.quality == "boa").count()
    last = db.query(func.max(Reading.captured_at)).filter(Reading.source != "demonstracao").scalar()
    return PublicOverview(
        sensors_total=sum(v for k, v in counts.items() if k != "inativo"),
        sensors_online=counts["online"] + counts["atencao"],
        analyses_30d=total,
        varieties_monitored=varieties_n,
        avg_confidence_30d=round(float(avg_conf), 4) if avg_conf is not None else None,
        quality_ratio_30d=round(good / total, 4) if total else None,
        last_analysis_at=from_db(last),
    )


@router.get("/system/status", response_model=SystemStatus, response_model_by_alias=True)
def system_status(db: Session = Depends(get_db), _: User = Depends(admin_only)):
    settings = get_settings()
    detector = get_detector()
    try:
        db.execute(text("SELECT 1"))
        database = engine.dialect.name
    except Exception:  # pragma: no cover
        database = "indisponível"
    return SystemStatus(
        version=__version__,
        environment=settings.environment,
        database=database,
        detector=detector.name,
        detector_description=detector.description,
        model_loaded=isinstance(detector, YoloDetector),
        timezone=settings.oasis_timezone,
        farm_name=settings.oasis_farm_name or None,
        public_read=settings.public_read,
        storage_mb=storage_size_mb(),
        counts={
            "usuarios": db.query(func.count(User.id)).scalar() or 0,
            "sensores": db.query(func.count(Sensor.id)).scalar() or 0,
            "leituras": db.query(func.count(Reading.id)).scalar() or 0,
            "leituras_demonstracao": db.query(func.count(Reading.id)).filter(Reading.source == "demonstracao").scalar() or 0,
            "medicoes_ambientais": db.query(func.count(EnvironmentReading.id)).scalar() or 0,
            "importacoes": db.query(func.count(ImportJob.id)).scalar() or 0,
        },
    )


@router.get("/audit", response_model=AuditPage, response_model_by_alias=True)
def audit_log(
    action: str | None = Query(default=None, max_length=60),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200, alias="pageSize"),
    db: Session = Depends(get_db),
    _: User = Depends(admin_only),
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [AuditOut(id=r.id, created_at=from_db(r.created_at), actor=r.actor, action=r.action, target=r.target, details=r.details, ip=r.ip) for r in rows]
    return AuditPage(items=items, total=total, page=page, page_size=page_size)
