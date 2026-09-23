"""Persistência e serialização das leituras."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..catalog import variety_from_class
from ..models import Detection, Reading, Sensor
from ..schemas import DetectionOut, ReadingOut
from .classifier import Analysis
from .detector import RawDetection


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def persist_reading(
    db: Session,
    *,
    sensor: Sensor,
    analysis: Analysis,
    detections: list[RawDetection],
    captured_at: datetime,
    model_version: str,
    processing_ms: int,
    image_path: str | None = None,
    image_seed: int = 0,
    commit: bool = True,
) -> Reading:
    reading = Reading(
        sensor_id=sensor.id,
        captured_at=as_utc(captured_at),
        variety_id=analysis.variety_id,
        quality=analysis.quality,
        confidence=analysis.confidence,
        maturation=analysis.maturation,
        visual_condition=analysis.visual_condition,
        classification=analysis.classification,
        observations=analysis.observations,
        clusters_detected=max(1, len(analysis.clusters)) if analysis.clusters else 0,
        image_path=image_path,
        image_seed=image_seed,
        model_version=model_version,
        processing_ms=processing_ms,
        stage="concluida",
    )
    for d in detections:
        kind = "cacho" if variety_from_class(d.label) else "anomalia"
        x, y, w, h = d.box
        reading.detections.append(Detection(kind=kind, label=d.label, confidence=d.confidence, x=x, y=y, w=w, h=h))
    db.add(reading)
    db.flush()
    reading.code = f"OS-{reading.id:05d}"
    if commit:
        db.commit()
    return reading


def to_reading_out(r: Reading) -> ReadingOut:
    return ReadingOut(
        id=r.code or f"OS-{r.id:05d}",
        sensor_id=r.sensor_id,
        block=r.sensor.block,
        location=r.sensor.location,
        captured_at=as_utc(r.captured_at),
        variety_id=r.variety_id,
        quality=r.quality,  # type: ignore[arg-type]
        confidence=r.confidence,
        maturation=r.maturation,
        visual_condition=r.visual_condition,
        classification=r.classification,
        observations=r.observations,
        detections=[DetectionOut(kind=d.kind, label=d.label, confidence=d.confidence, box=(d.x, d.y, d.w, d.h)) for d in r.detections],  # type: ignore[arg-type]
        clusters_detected=r.clusters_detected,
        image_seed=r.image_seed,
        image_url=f"/api/v1/readings/{r.code}/image" if r.image_path else None,
        model_version=r.model_version,
        processing_ms=r.processing_ms,
        stage=r.stage,
    )
