"""
Ingestão de imagens: ponto de entrada do fluxo
Sensor IoT → Captura → Envio → Processamento → YOLO/IA → Identificação → Classificação → Armazenamento.
"""

import io
import random
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import authorize_device, current_user_optional, device_token_header, require_roles
from ..models import Sensor, SensorTelemetry, User, Variety
from ..schemas import ReadingOut
from ..services.classifier import analyze
from ..services.detector import MockDetector, get_detector
from ..services.readings import persist_reading, to_reading_out
from ..services.storage import save_image
from .sensors import get_sensor_or_404, refresh_status

router = APIRouter(tags=["Ingestão"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _variety_type(db: Session, variety_id: str) -> str:
    v = db.get(Variety, variety_id)
    return v.type if v else "Tinta"


@router.post("/ingest", response_model=ReadingOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def ingest(
    sensor_id: str = Form(...),
    image: UploadFile = File(...),
    captured_at: datetime | None = Form(default=None),
    battery: int | None = Form(default=None, ge=0, le=100),
    signal: int | None = Form(default=None, ge=-120, le=0),
    db: Session = Depends(get_db),
    token: str | None = Depends(device_token_header),
    user: User | None = Depends(current_user_optional),
):
    """Recebe a imagem de um sensor (ou do gateway de edge), executa o detector e armazena o resultado."""
    started = time.perf_counter()
    sensor = get_sensor_or_404(db, sensor_id)
    authorize_device(sensor, token, user)

    data = image.file.read()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Imagem vazia ou maior que 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Arquivo enviado não é uma imagem válida.")

    at = captured_at or datetime.now(timezone.utc)
    path = save_image(data, at)
    detector = get_detector()
    output = detector.detect(data, variety_hint=sensor.variety_id)
    analysis = analyze(output.detections, sensor.variety_id, _variety_type(db, sensor.variety_id), data, seed=len(data))

    sensor.last_communication = datetime.now(timezone.utc)
    if battery is not None:
        sensor.battery = battery
    if signal is not None:
        sensor.signal_dbm = signal
    if battery is not None or signal is not None:
        db.add(SensorTelemetry(sensor_id=sensor.id, battery=battery, signal_dbm=signal))
    refresh_status(sensor)

    reading = persist_reading(
        db, sensor=sensor, analysis=analysis, detections=output.detections, captured_at=at,
        model_version=output.model_version, processing_ms=int((time.perf_counter() - started) * 1000),
        image_path=path, image_seed=random.randint(0, 10**9),
    )
    return to_reading_out(reading)


@router.post("/simulate/{sensor_id}", response_model=ReadingOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def simulate(sensor_id: str, db: Session = Depends(get_db), _: User = Depends(require_roles("admin", "gestor", "operador"))):
    """Gera uma leitura simulada para o sensor — demonstra o pipeline sem hardware."""
    sensor: Sensor = get_sensor_or_404(db, sensor_id)
    if sensor.status == "offline":
        raise HTTPException(status.HTTP_409_CONFLICT, "Sensor offline não pode capturar imagens.")
    rng = random.Random()
    started = time.perf_counter()
    output = MockDetector().simulate(rng, sensor.variety_id)
    analysis = analyze(output.detections, sensor.variety_id, seed=rng.randint(0, 10**9))
    sensor.last_communication = datetime.now(timezone.utc)
    reading = persist_reading(
        db, sensor=sensor, analysis=analysis, detections=output.detections, captured_at=datetime.now(timezone.utc),
        model_version=output.model_version, processing_ms=int((time.perf_counter() - started) * 1000) + rng.randint(180, 480),
        image_seed=rng.randint(0, 10**9),
    )
    return to_reading_out(reading)
