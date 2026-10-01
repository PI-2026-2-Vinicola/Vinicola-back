"""Esquemas Pydantic — contrato JSON em camelCase, o mesmo tipado no frontend."""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

Quality = Literal["boa", "atencao", "critica"]
Role = Literal["admin", "gestor", "operador"]
SensorStatus = Literal["online", "atencao", "offline", "inativo"]
ImportKind = Literal["readings", "sensors", "environment"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SENSOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,19}$")


def check_password(value: str) -> str:
    if len(value) < 8 or not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("A senha deve ter pelo menos 8 caracteres, com letras e números.")
    return value


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ------------------------------------------------------------------ usuários
class UserOut(CamelModel):
    id: int
    name: str
    email: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class LoginIn(BaseModel):
    email: str = Field(max_length=160)
    password: str = Field(max_length=200)


class TokenOut(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class PasswordChangeIn(CamelModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)

    _pw = field_validator("new_password")(check_password)


class UserCreateIn(CamelModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=160)
    role: Role
    password: str = Field(max_length=200)

    _pw = field_validator("password")(check_password)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("E-mail inválido.")
        return v


class UserUpdateIn(CamelModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=200)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str | None) -> str | None:
        return check_password(v) if v else v


# ------------------------------------------------------------------ leituras
class DetectionOut(CamelModel):
    kind: Literal["cacho", "anomalia"]
    label: str
    confidence: float
    box: tuple[float, float, float, float]


class ReadingOut(CamelModel):
    id: str
    sensor_id: str
    sensor_name: str
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
    image_url: str | None = None
    thumb_url: str | None = None
    source: str
    model_version: str
    processing_ms: int
    stage: str


class ReadingPage(CamelModel):
    items: list[ReadingOut]
    total: int
    page: int
    page_size: int


# ------------------------------------------------------------------ sensores
class SensorOut(CamelModel):
    id: str
    name: str
    block: str
    location: str
    lat: float | None
    lng: float | None
    has_valid_location: bool
    variety_id: str
    device: str | None
    firmware: str | None
    battery: int | None
    signal: int | None
    capture_interval_min: int
    active: bool
    status: SensorStatus
    status_reason: str
    installed_at: date | None
    last_communication: datetime | None
    analyses_count: int
    last_reading_at: datetime | None
    quality_ratio: float | None


class SensorBase(CamelModel):
    name: str = Field(min_length=2, max_length=80)
    block: str = Field(min_length=1, max_length=40)
    location: str = Field(min_length=1, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    variety_id: str
    device: str | None = Field(default=None, max_length=60)
    firmware: str | None = Field(default=None, max_length=20)
    capture_interval_min: int = Field(default=90, ge=5, le=1440)
    installed_at: date | None = None

    @model_validator(mode="after")
    def _coords(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("Informe latitude e longitude juntas.")
        if self.lat == 0 and self.lng == 0:
            raise ValueError("Coordenadas 0,0 não são válidas para um talhão.")
        return self


class SensorCreateIn(SensorBase):
    id: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        v = v.strip().upper()
        if not SENSOR_ID_RE.match(v):
            raise ValueError("Use de 2 a 20 caracteres: letras, números, '-' ou '_' (ex.: S-001).")
        return v


class SensorUpdateIn(CamelModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    block: str | None = Field(default=None, min_length=1, max_length=40)
    location: str | None = Field(default=None, min_length=1, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    clear_location: bool = False
    variety_id: str | None = None
    device: str | None = Field(default=None, max_length=60)
    firmware: str | None = Field(default=None, max_length=20)
    capture_interval_min: int | None = Field(default=None, ge=5, le=1440)
    installed_at: date | None = None
    active: bool | None = None


class SensorTokenOut(CamelModel):
    sensor: SensorOut
    device_token: str


class HeartbeatIn(CamelModel):
    battery: int | None = Field(default=None, ge=0, le=100)
    signal: int | None = Field(default=None, ge=-120, le=0)
    firmware: str | None = Field(default=None, max_length=20)
    temperature_c: float | None = Field(default=None, ge=-20, le=60)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)


class EnvironmentIn(CamelModel):
    measured_at: datetime | None = None
    temperature_c: float | None = Field(default=None, ge=-20, le=60)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    luminosity_lux: float | None = Field(default=None, ge=0, le=200000)
    soil_moisture_pct: float | None = Field(default=None, ge=0, le=100)


class EnvironmentPoint(CamelModel):
    measured_at: datetime
    temperature_c: float | None
    humidity_pct: float | None
    luminosity_lux: float | None
    soil_moisture_pct: float | None
    source: str


class TelemetryPoint(CamelModel):
    received_at: datetime
    battery: int | None
    signal: int | None
    firmware: str | None


# ------------------------------------------------------------------ catálogo e indicadores
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


class PeriodTotals(CamelModel):
    readings: int
    quality_ratio: float | None


class EnvironmentSummary(CamelModel):
    avg_temperature_c: float | None
    avg_humidity_pct: float | None
    measurements: int
    last_measured_at: datetime | None


class SummaryOut(CamelModel):
    period_start: datetime
    period_end: datetime
    sensors_total: int
    sensors_online: int
    sensors_attention: int
    sensors_offline: int
    readings: int
    clusters: int
    quality: QualityCount
    quality_ratio: float | None
    avg_confidence: float | None
    alerts: int
    last_reading_at: datetime | None
    previous: PeriodTotals | None
    environment: EnvironmentSummary


class BucketOut(CamelModel):
    key: str
    label: str
    boa: int
    atencao: int
    critica: int
    total: int
    avg_confidence: float | None


class GroupOut(CamelModel):
    key: str
    total: int
    boa: int
    atencao: int
    critica: int
    avg_confidence: float | None


class EnvironmentDay(CamelModel):
    key: str
    label: str
    measurements: int
    avg_temperature_c: float | None
    min_temperature_c: float | None
    max_temperature_c: float | None
    avg_humidity_pct: float | None
    avg_luminosity_lux: float | None
    avg_soil_moisture_pct: float | None


class PublicOverview(CamelModel):
    sensors_total: int
    sensors_online: int
    analyses_30d: int = Field(alias="analyses30d")
    varieties_monitored: int
    avg_confidence_30d: float | None = Field(alias="avgConfidence30d")
    quality_ratio_30d: float | None = Field(alias="qualityRatio30d")
    last_analysis_at: datetime | None


# ------------------------------------------------------------------ importação, auditoria, sistema
class RowError(BaseModel):
    row: int
    field: str | None = None
    message: str


class ImportPreviewOut(CamelModel):
    kind: ImportKind
    filename: str
    total: int
    valid: int
    duplicates: int
    invalid: int
    columns: list[str]
    sample: list[dict]
    errors: list[RowError]


class ImportJobOut(CamelModel):
    id: int
    kind: ImportKind
    filename: str
    status: str
    total_rows: int
    inserted: int
    updated: int
    duplicates: int
    invalid: int
    errors: list[RowError]
    created_by_name: str | None
    created_at: datetime


class AuditOut(CamelModel):
    id: int
    created_at: datetime
    actor: str | None
    action: str
    target: str | None
    details: str | None
    ip: str | None


class AuditPage(CamelModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int


class SystemStatus(CamelModel):
    version: str
    environment: str
    database: str
    detector: str
    detector_description: str
    model_loaded: bool
    timezone: str
    farm_name: str | None
    public_read: bool
    storage_mb: float
    counts: dict[str, int]
