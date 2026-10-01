"""Modelo relacional do OASIS (espelha os scripts SQL do repositório Vinicola-bd)."""

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','gestor','operador')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Variety(Base):
    __tablename__ = "varieties"
    __table_args__ = (CheckConstraint("type IN ('Tinta','Branca')", name="ck_varieties_type"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(10))
    color: Mapped[str] = mapped_column(String(80))
    maturation_cycle: Mapped[str] = mapped_column(String(60))
    origin: Mapped[str] = mapped_column(String(120))


class Sensor(Base):
    """
    Dispositivo de captura. O status (online/atenção/offline) não é gravado: é calculado
    a partir da última comunicação, da bateria e do sinal (services/sensors.py).
    """

    __tablename__ = "sensors"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    block: Mapped[str] = mapped_column(String(40))
    location: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    variety_id: Mapped[str] = mapped_column(ForeignKey("varieties.id"))
    device: Mapped[str | None] = mapped_column(String(60), nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(20), nullable=True)
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_dbm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capture_interval_min: Mapped[int] = mapped_column(Integer, default=90)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_communication: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    readings: Mapped[list["Reading"]] = relationship(back_populates="sensor")


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        CheckConstraint("quality IN ('boa','atencao','critica')", name="ck_readings_quality"),
        CheckConstraint("stage IN ('recebida','processando','analisando','concluida')", name="ck_readings_stage"),
        UniqueConstraint("sensor_id", "captured_at", name="uq_readings_sensor_time"),
        Index("ix_readings_captured_at", "captured_at"),
        Index("ix_readings_variety_time", "variety_id", "captured_at"),
        Index("ix_readings_quality_time", "quality", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    variety_id: Mapped[str] = mapped_column(ForeignKey("varieties.id"))
    quality: Mapped[str] = mapped_column(String(10))
    confidence: Mapped[float] = mapped_column(Float)
    maturation: Mapped[str] = mapped_column(String(20))
    visual_condition: Mapped[str] = mapped_column(String(60))
    classification: Mapped[str] = mapped_column(String(30))
    observations: Mapped[str] = mapped_column(Text)
    clusters_detected: Mapped[int] = mapped_column(Integer, default=1)
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="sensor")
    model_version: Mapped[str] = mapped_column(String(80))
    processing_ms: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(12), default="concluida")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sensor: Mapped[Sensor] = relationship(back_populates="readings")
    detections: Mapped[list["Detection"]] = relationship(back_populates="reading", cascade="all, delete-orphan", order_by="Detection.id")


class Detection(Base):
    """Caixa detectada (coordenadas normalizadas 0–1: x, y do canto superior esquerdo, largura, altura)."""

    __tablename__ = "detections"
    __table_args__ = (CheckConstraint("kind IN ('cacho','anomalia')", name="ck_detections_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reading_id: Mapped[int] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)

    reading: Mapped[Reading] = relationship(back_populates="detections")


class SensorTelemetry(Base):
    """Bateria, sinal Wi-Fi e firmware informados pelos dispositivos."""

    __tablename__ = "sensor_telemetry"
    __table_args__ = (Index("ix_sensor_telemetry_sensor_time", "sensor_id", "received_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_dbm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(20), nullable=True)


class EnvironmentReading(Base):
    """Medições ambientais do talhão (sensor DHT22/BH1750/umidade do solo ou importação)."""

    __tablename__ = "environment_readings"
    __table_args__ = (
        UniqueConstraint("sensor_id", "measured_at", name="uq_environment_sensor_time"),
        Index("ix_environment_sensor_time", "sensor_id", "measured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    luminosity_lux: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_moisture_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="sensor")


class ImportJob(Base):
    """Registro de cada importação concluída (resumo e erros por linha)."""

    __tablename__ = "import_jobs"
    __table_args__ = (CheckConstraint("kind IN ('readings','sensors','environment')", name="ck_import_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20))  # concluida · parcial · sem_alteracoes · falhou
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Trilha de auditoria: logins, alterações de usuários e sensores, importações."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str | None] = mapped_column(String(160), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(160), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
