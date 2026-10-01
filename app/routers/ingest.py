"""
Ingestão de imagens — ponto de entrada do fluxo
Sensor IoT (ou envio manual) → validação → processamento → detector → classificação → armazenamento.
"""

import hashlib
import logging
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import authorize_device, current_user_optional, device_token_header
from ..models import EnvironmentReading, Reading, User, Variety
from ..schemas import ReadingOut
from ..services.classifier import analyze
from ..services.detector import get_detector
from ..services.readings import persist_reading, to_reading_out
from ..services.storage import InvalidImage, image_file, load_image, save_image
from ..services.timeutil import as_utc, utcnow
from .sensors import apply_device_status, get_sensor_or_404

router = APIRouter(tags=["Ingestão"])
log = logging.getLogger("oasis.ingest")


async def _read_limited(upload: UploadFile, limit_mb: int) -> bytes:
    limit = limit_mb * 1024 * 1024
    data = await upload.read(limit + 1)
    if not data:
        raise HTTPException(422, "Nenhuma imagem recebida.")
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"A imagem excede o limite de {limit_mb} MB.")
    return data


@router.post("/ingest", response_model=ReadingOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
async def ingest(
    sensor_id: str = Form(..., max_length=20),
    image: UploadFile = File(...),
    captured_at: datetime | None = Form(default=None),
    battery: int | None = Form(default=None, ge=0, le=100),
    signal: int | None = Form(default=None, ge=-120, le=0),
    firmware: str | None = Form(default=None, max_length=20),
    temperature_c: float | None = Form(default=None, ge=-20, le=60),
    humidity_pct: float | None = Form(default=None, ge=0, le=100),
    db: Session = Depends(get_db),
    token: str | None = Depends(device_token_header),
    user: User | None = Depends(current_user_optional),
):
    """
    Recebe a imagem de um sensor (cabeçalho `X-Device-Token`) ou enviada por um usuário autenticado,
    analisa a imagem com o detector configurado, classifica e grava o resultado.
    """
    started = time.perf_counter()
    sensor = get_sensor_or_404(db, sensor_id)
    authorize_device(sensor, token, user)
    if not sensor.active:
        raise HTTPException(status.HTTP_409_CONFLICT, f"O sensor {sensor.id} está desativado.")

    data = await _read_limited(image, get_settings().max_upload_mb)
    try:
        img = load_image(data)
    except InvalidImage as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    now = utcnow()
    at = as_utc(captured_at) if captured_at else now
    if at > now + timedelta(minutes=5):
        raise HTTPException(422, "A data de captura está no futuro.")
    at = at.replace(microsecond=0)
    if db.query(Reading.id).filter(Reading.sensor_id == sensor.id, Reading.captured_at == at).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Já existe uma leitura do sensor {sensor.id} neste horário.")

    variety = db.get(Variety, sensor.variety_id)
    variety_type = variety.type if variety else "Tinta"
    try:
        output = get_detector().detect(img, sensor.variety_id, variety_type)
    except Exception:
        log.exception("Falha no detector ao processar imagem do sensor %s", sensor.id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Não foi possível processar a imagem agora. Tente novamente.")
    seed = int(hashlib.sha1(data).hexdigest()[:8], 16)
    analysis = analyze(output.detections, sensor.variety_id, variety_type, img, seed=seed)

    path, thumb = save_image(img, at)
    via_device = user is None
    if via_device:
        apply_device_status(db, sensor, battery, signal, firmware, now)
    if temperature_c is not None or humidity_pct is not None:
        exists = db.query(EnvironmentReading.id).filter(EnvironmentReading.sensor_id == sensor.id, EnvironmentReading.measured_at == at).first()
        if not exists:
            db.add(EnvironmentReading(sensor_id=sensor.id, measured_at=at, temperature_c=temperature_c, humidity_pct=humidity_pct, source="sensor" if via_device else "manual"))
    try:
        reading = persist_reading(
            db,
            sensor=sensor,
            analysis=analysis,
            detections=output.detections,
            captured_at=at,
            model_version=output.model_version,
            processing_ms=int((time.perf_counter() - started) * 1000),
            source="sensor" if via_device else "upload",
            image_path=path,
            thumb_path=thumb,
            created_by=user.id if user else None,
        )
    except IntegrityError:
        db.rollback()
        for p in (path, thumb):
            image_file(p).unlink(missing_ok=True)
        raise HTTPException(status.HTTP_409_CONFLICT, f"Já existe uma leitura do sensor {sensor.id} neste horário.")
    log.info("Leitura %s gravada (sensor %s, %s, %s)", reading.code, sensor.id, reading.quality, reading.source)
    return to_reading_out(reading)
