"""Modelo relacional da OSAIS (espelha os scripts SQL do repositório Vinicola-bd)."""

from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','gestor','operador')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
    __tablename__ = "sensors"
    __table_args__ = (CheckConstraint("status IN ('online','atencao','offline')", name="ck_sensors_status"),)

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    block: Mapped[str] = mapped_column(String(40))
    location: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    variety_id: Mapped[str] = mapped_column(ForeignKey("varieties.id"))
    device: Mapped[str] = mapped_column(String(60))
    firmware: Mapped[str] = mapped_column(String(20))
    battery: Mapped[int] = mapped_column(Integer, default=100)
    signal_dbm: Mapped[int] = mapped_column(Integer, default=-60)
    capture_interval_min: Mapped[int] = mapped_column(Integer, default=90)
    status: Mapped[str] = mapped_column(String(10), default="online")
    installed_at: Mapped[date] = mapped_column(Date)
    last_communication: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    readings: Mapped[list["Reading"]] = relationship(back_populates="sensor")


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        CheckConstraint("quality IN ('boa','atencao','critica')", name="ck_readings_quality"),
        CheckConstraint("stage IN ('recebida','processando','analisando','concluida')", name="ck_readings_stage"),
        Index("ix_readings_captured_at", "captured_at"),
        Index("ix_readings_sensor_time", "sensor_id", "captured_at"),
        Index("ix_readings_variety_time", "variety_id", "captured_at"),
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
    image_seed: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str] = mapped_column(String(60))
    processing_ms: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(12), default="concluida")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sensor: Mapped[Sensor] = relationship(back_populates="readings")
    detections: Mapped[list["Detection"]] = relationship(back_populates="reading", cascade="all, delete-orphan", order_by="Detection.id")


class Detection(Base):
    """Caixa detectada pelo YOLO (coordenadas normalizadas 0–1: x, y do canto superior esquerdo, largura, altura)."""

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
    """Histórico de sinais de vida (bateria, sinal) enviados pelos dispositivos."""

    __tablename__ = "sensor_telemetry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal_dbm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(20), nullable=True)
