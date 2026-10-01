"""Persistência e serialização das leituras."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..catalog import variety_from_class
from ..models import Detection, Reading, Sensor
from ..schemas import DetectionOut, ReadingOut
from .classifier import Analysis
from .detector import RawDetection
from .timeutil import as_utc, from_db

API = "/api/v1"


def next_code(reading: Reading) -> str:
    return f"OA-{reading.id:05d}"


def persist_reading(
    db: Session,
    *,
    sensor: Sensor,
    analysis: Analysis,
    detections: list[RawDetection],
    captured_at: datetime,
    model_version: str,
    processing_ms: int,
    source: str,
    image_path: str | None = None,
    thumb_path: str | None = None,
    created_by: int | None = None,
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
        clusters_detected=len(analysis.clusters),
        image_path=image_path,
        thumb_path=thumb_path,
        source=source,
        model_version=model_version,
        processing_ms=processing_ms,
        stage="concluida",
        created_by=created_by,
    )
    for d in detections:
        kind = "cacho" if (variety_from_class(d.label) or d.label == "cacho") else "anomalia"
        x, y, w, h = d.box
        reading.detections.append(Detection(kind=kind, label=d.label, confidence=d.confidence, x=x, y=y, w=w, h=h))
    db.add(reading)
    db.flush()
    reading.code = next_code(reading)
    if commit:
        db.commit()
    return reading


def to_reading_out(r: Reading) -> ReadingOut:
    code = r.code or next_code(r)
    return ReadingOut(
        id=code,
        sensor_id=r.sensor_id,
        sensor_name=r.sensor.name,
        block=r.sensor.block,
        location=r.sensor.location,
        captured_at=from_db(r.captured_at),
        variety_id=r.variety_id,
        quality=r.quality,  # type: ignore[arg-type]
        confidence=r.confidence,
        maturation=r.maturation,
        visual_condition=r.visual_condition,
        classification=r.classification,
        observations=r.observations,
        detections=[DetectionOut(kind=d.kind, label=d.label, confidence=d.confidence, box=(d.x, d.y, d.w, d.h)) for d in r.detections],  # type: ignore[arg-type]
        clusters_detected=r.clusters_detected,
        image_url=f"{API}/readings/{code}/image" if r.image_path else None,
        thumb_url=f"{API}/readings/{code}/image?size=thumb" if r.thumb_path else None,
        source=r.source,
        model_version=r.model_version,
        processing_ms=r.processing_ms,
        stage=r.stage,
    )
