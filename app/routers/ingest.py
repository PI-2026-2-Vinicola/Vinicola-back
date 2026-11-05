"""
Ingestão de imagens — ponto de entrada do fluxo
Sensor IoT (ou envio manual) → validação → processamento → detector → classificação → armazenamento.
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..catalog import ANOMALY_TEXT, GENERIC_CLUSTER_CLASS, MILD_ANOMALIES, MODEL_CLASSES, VARIETIES, variety_from_class
from ..config import get_settings
from ..database import get_db
from ..deps import authorize_device, current_user, current_user_optional, device_token_header
from ..models import EnvironmentReading, Reading, User, Variety
from ..schemas import AnalysisComponentOut, AnalysisModelOut, ModelCardOut, ModelClassOut, ModelDatasetOut, ModelMetricsOut, ReadingOut
from ..services.classifier import ANOMALY_MIN_CONFIDENCE, analyze
from ..services.detector import ColorDetector, OnnxYoloDetector, YoloDetector, get_detector, resolve_model_path
from ..services.readings import persist_reading, to_reading_out
from ..services.storage import InvalidImage, image_file, load_image, save_image
from ..services.timeutil import as_utc, utcnow
from .sensors import apply_device_status, get_sensor_or_404

router = APIRouter(tags=["Ingestão"])
log = logging.getLogger("oasis.ingest")

ACCEPTED_FORMATS = ["JPEG", "PNG", "WEBP"]
VARIETY_NAME = {v["id"]: v["name"] for v in VARIETIES}


def _class_out(label: str, source: str = "modelo") -> ModelClassOut:
    if label == GENERIC_CLUSTER_CLASS:
        return ModelClassOut(name=label, label="Cacho de uva", kind="cacho", source=source)
    variety = variety_from_class(label)
    if variety:
        return ModelClassOut(name=label, label=VARIETY_NAME[variety], kind="variedade", source=source)
    return ModelClassOut(name=label, label=ANOMALY_TEXT[label][0], kind="anomalia_leve" if label in MILD_ANOMALIES else "anomalia_grave", source=source)


def _card_out(detector: OnnxYoloDetector) -> ModelCardOut:
    card = detector.card
    order = {"test": 0, "val": 1}
    return ModelCardOut(
        name=card.name,
        version=card.version,
        task=card.task,
        architecture=card.architecture,
        framework=card.framework,
        input_size=card.input_size,
        trained_at=card.trained_at,
        pretrained=card.pretrained,
        epochs=card.training.get("epocas_executadas"),
        metrics=[ModelMetricsOut(**m.model_dump()) for _, m in sorted(card.metrics.items(), key=lambda kv: order.get(kv[0], 9))],
        dataset=ModelDatasetOut(**card.dataset.model_dump()),
        license=card.license,
        inference_ms_cpu=card.inference_ms_cpu,
        sha256=card.sha256,
    )


def _br(value: float, digits: int = 2) -> str:
    """Número com vírgula decimal, como no restante da interface."""
    return f"{value:.{digits}f}".replace(".", ",")


COLOR_FALLBACK_LIMITS = [
    "A análise de cor não reconhece a variedade: usa a variedade cadastrada no talhão do sensor.",
    "Avaliada com fotos reais de campo (Embrapa WGISD), a caixa encontrada pela análise de cor não coincidiu com os cachos "
    "anotados e as regras apontaram anomalias na maioria das fotos (ml/reports/calibracao_cor.json): use o modelo YOLO publicado.",
]


@router.get("/analysis/model", response_model=AnalysisModelOut, response_model_by_alias=True)
def analysis_model(_: User = Depends(current_user)):
    """Detector efetivamente em uso, o que ele identifica, de onde vem cada informação e os limites do envio de imagens."""
    settings = get_settings()
    detector = get_detector()
    onnx = isinstance(detector, OnnxYoloDetector)
    yolo = onnx or isinstance(detector, YoloDetector)
    weights = detector.path if onnx else resolve_model_path(settings.oasis_model_path)
    found = weights.is_file()
    limitations: list[str] = []
    components: list[AnalysisComponentOut] = []
    classes: list[ModelClassOut] = []
    if onnx and detector.clusters_only:
        limitations += detector.card.limitations
        t = detector.thresholds
        limitations.append(
            "A análise de cor dentro dos cachos é uma regra calibrada para disparar raramente em cachos reais sem defeito anotado "
            f"(manchas a partir de {t.brown_mild:.0%} da área, podridão a partir de {t.brown_severe:.0%}); a capacidade de "
            "encontrar defeitos reais ainda não foi medida, por falta de fotos anotadas de cachos com defeito."
        )
        components = [
            AnalysisComponentOut(item="Localização e contagem dos cachos", source=f"Modelo {detector.card.architecture} treinado ({detector.card.name} {detector.card.version})", kind="modelo"),
            AnalysisComponentOut(item="Variedade", source="Cadastro do talhão do sensor (o modelo não identifica variedades)", kind="cadastro"),
            AnalysisComponentOut(item="Manchas, mofo, maturação desigual e estágio de maturação", source="Análise de cor (regra) dentro de cada cacho detectado", kind="regra"),
        ]
        classes = [_class_out(GENERIC_CLUSTER_CLASS)] + [_class_out(c, "analise_cor") for c in detector.classes if c != GENERIC_CLUSTER_CLASS]
    elif yolo:
        limitations += [
            "As classes e a precisão dependem das imagens anotadas usadas no treinamento (ml/README.md).",
            f"Detecções com confiança abaixo de {_br(detector.confidence)} são descartadas pelo modelo.",
        ]
        components = [AnalysisComponentOut(item="Cachos, variedade e anomalias", source="Modelo YOLO treinado", kind="modelo")]
        classes = [_class_out(c) for c in getattr(detector, "classes", []) if c in MODEL_CLASSES]
    else:
        if not found:
            weights_note = f"Nenhum modelo YOLO foi encontrado ({weights.name}): a detecção por YOLO é ativada quando o modelo for publicado em models/."
        elif settings.oasis_detector.lower() == "color":
            weights_note = "O detector está fixado em análise de cor (OASIS_DETECTOR=color), embora exista um modelo publicado."
        else:
            weights_note = f"O modelo {weights.name} existe, mas não pôde ser carregado (consulte o log da API)."
        limitations += COLOR_FALLBACK_LIMITS + [
            "Depende de enquadramento e iluminação; em uvas brancas não distingue bagas verdes da folhagem.",
            weights_note,
        ]
        components = [
            AnalysisComponentOut(item="Localização do cacho", source="Análise de cor (regra), sem modelo treinado", kind="regra"),
            AnalysisComponentOut(item="Variedade", source="Cadastro do talhão do sensor", kind="cadastro"),
            AnalysisComponentOut(item="Anomalias e maturação", source="Análise de cor (regra)", kind="regra"),
        ]
        classes = [_class_out(c, "analise_cor") for c in ColorDetector.classes if c in MODEL_CLASSES]
    limitations += [
        f"Anomalias com confiança abaixo de {_br(ANOMALY_MIN_CONFIDENCE, 1)} não alteram a classificação.",
        "O resultado é uma previsão computacional a partir da imagem, não um diagnóstico fitossanitário, e não substitui a avaliação de um responsável técnico.",
    ]
    return AnalysisModelOut(
        detector=detector.name,
        version=getattr(detector, "version", detector.name),
        description=detector.description,
        yolo_loaded=yolo,
        yolo_weights_file=weights.name,
        yolo_weights_found=found,
        classes=classes,
        confidence_threshold=detector.confidence if yolo else None,
        anomaly_min_confidence=ANOMALY_MIN_CONFIDENCE,
        max_upload_mb=settings.max_upload_mb,
        accepted_formats=ACCEPTED_FORMATS,
        timeout_seconds=settings.oasis_analysis_timeout_seconds,
        limitations=limitations,
        components=components,
        card=_card_out(detector) if onnx else None,
    )


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

    settings = get_settings()
    data = await _read_limited(image, settings.max_upload_mb)
    try:
        img = await run_in_threadpool(load_image, data)
    except InvalidImage as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    now = utcnow()
    at = as_utc(captured_at) if captured_at else now
    if at > now + timedelta(minutes=5):
        raise HTTPException(422, "A data de captura está no futuro.")
    at = at.replace(microsecond=0)
    if db.query(Reading.id).filter(Reading.sensor_id == sensor.id, Reading.captured_at == at).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Já existe uma leitura do sensor {sensor.id} neste horário.")

    variety_id = sensor.variety_id
    variety = db.get(Variety, variety_id)
    variety_type = variety.type if variety else "Tinta"
    detector = get_detector()
    seed = int(hashlib.sha1(data).hexdigest()[:8], 16)

    def run_analysis():
        output = detector.detect(img, variety_id, variety_type)
        return output, analyze(output.detections, variety_id, variety_type, img, seed=seed)

    # A análise roda fora do event loop (CPU) e tem tempo limite; uma análise lenta não trava as demais requisições.
    timeout = settings.oasis_analysis_timeout_seconds
    try:
        output, analysis = await asyncio.wait_for(run_in_threadpool(run_analysis), timeout=timeout)
    except TimeoutError:
        log.error("Análise da imagem do sensor %s excedeu %.0f s", sensor.id, timeout)
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"A análise excedeu o tempo limite de {timeout:.0f} s. Tente novamente; se persistir, envie uma imagem menor.")
    except Exception:
        log.exception("Falha no detector ao processar imagem do sensor %s", sensor.id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Não foi possível processar a imagem agora. Tente novamente.")

    path, thumb = await run_in_threadpool(save_image, img, at)
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
