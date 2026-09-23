"""Esquemas Pydantic — contrato JSON em camelCase, idêntico ao usado pelo frontend."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Quality = Literal["boa", "atencao", "critica"]
Role = Literal["admin", "gestor", "operador"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class UserOut(CamelModel):
    name: str
    email: str
    role: Role


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(CamelModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RecoverIn(BaseModel):
    email: str


class DetectionOut(CamelModel):
    kind: Literal["cacho", "anomalia"]
    label: str
    confidence: float
    box: tuple[float, float, float, float]


class ReadingOut(CamelModel):
    id: str
    sensor_id: str
    block: str
    location: str
    captured_at: datetime
    variety_id: str
    quality: Quality
    confidence: float
    maturation: str
    visual_condition: str
    classification: str
    observations: str
    detections: list[DetectionOut]
    clusters_detected: int
    image_seed: int
    image_url: str | None = None
    model_version: str
    processing_ms: int
    stage: str


class SensorOut(CamelModel):
    id: str
    name: str
    block: str
    location: str
    lat: float
    lng: float
    status: Literal["online", "atencao", "offline"]
    variety_id: str
    device: str
    firmware: str
    battery: int
    signal: int
    capture_interval_min: int
    installed_at: date
    last_communication: datetime | None


class HeartbeatIn(CamelModel):
    battery: int | None = Field(default=None, ge=0, le=100)
    signal: int | None = Field(default=None, ge=-120, le=0)
    firmware: str | None = None


class VarietyOut(CamelModel):
    id: str
    name: str
    type: str
    color: str
    maturation_cycle: str
    origin: str


class QualityCount(BaseModel):
    boa: int = 0
    atencao: int = 0
    critica: int = 0
    total: int = 0


class SummaryOut(CamelModel):
    days: int
    sensors_total: int
    sensors_active: int
    readings: int
    clusters: int
    quality: QualityCount
    quality_ratio: float
    avg_confidence: float
    alerts: int


class DayBucketOut(CamelModel):
    day: date
    boa: int
    atencao: int
    critica: int
    total: int
    avg_confidence: float


__all__ = [
    "UserOut",
    "LoginIn",
    "TokenOut",
    "RecoverIn",
    "DetectionOut",
    "ReadingOut",
    "SensorOut",
    "HeartbeatIn",
    "VarietyOut",
    "QualityCount",
    "SummaryOut",
    "DayBucketOut",
]
