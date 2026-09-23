"""
Regras que transformam as detecções do modelo em informação para o produtor:
variedade, qualidade, condição visual, maturação, classificação e observações.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass

from ..catalog import ANOMALY_TEXT, CLASSIFICATION_BY_QUALITY, GOOD_OBSERVATIONS, MATURATIONS, SEVERE_ANOMALIES, variety_from_class
from .detector import RawDetection

ANOMALY_MIN_CONFIDENCE = 0.5


@dataclass
class Analysis:
    variety_id: str
    confidence: float
    quality: str
    maturation: str
    visual_condition: str
    classification: str
    observations: str
    clusters: list[RawDetection]
    anomalies: list[RawDetection]


def estimate_maturation(image_bytes: bytes | None, variety_type: str, cluster: RawDetection | None, rng: random.Random) -> str:
    """
    Estimativa simples do estágio de maturação pela cor média do cacho.
    É um ponto de partida: pode ser substituída por um classificador treinado (YOLO-cls).
    """
    if image_bytes and cluster:
        try:
            from PIL import Image, ImageStat

            img = Image.open(io.BytesIO(image_bytes)).convert("HSV")
            x, y, w, h = cluster.box
            W, H = img.size
            crop = img.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
            hue, sat, val = ImageStat.Stat(crop).mean
            if variety_type == "Tinta":
                # Uvas tintas escurecem ao amadurecer (valor baixo); verde indica início do ciclo.
                if 40 < hue < 110 and val > 90:
                    return "desenvolvimento"
                if val > 120:
                    return "pintor"
                if val > 85:
                    return "maturacao"
                return "adequada" if val > 40 else "sobrematuracao"
            # Uvas brancas passam de verde para amarelo-dourado.
            if hue > 55:
                return "desenvolvimento" if sat > 120 else "maturacao"
            return "adequada" if hue > 28 else "sobrematuracao"
        except Exception:
            pass
    return rng.choice(MATURATIONS[1:4])


def analyze(detections: list[RawDetection], variety_hint: str | None, variety_type: str = "Tinta", image_bytes: bytes | None = None, seed: int = 0) -> Analysis:
    rng = random.Random(seed)
    clusters = sorted([d for d in detections if variety_from_class(d.label)], key=lambda d: d.confidence, reverse=True)
    anomalies = [d for d in detections if d.label in ANOMALY_TEXT and d.confidence >= ANOMALY_MIN_CONFIDENCE]

    main = clusters[0] if clusters else None
    variety_id = variety_from_class(main.label) if main else None
    variety_id = variety_id or variety_hint or "cabernet-sauvignon"
    confidence = main.confidence if main else 0.0

    if any(a.label in SEVERE_ANOMALIES for a in anomalies):
        quality = "critica"
    elif anomalies:
        quality = "atencao"
    else:
        quality = "boa"

    if anomalies:
        top = max(anomalies, key=lambda a: (a.label in SEVERE_ANOMALIES, a.confidence))
        visual_condition, observations = ANOMALY_TEXT[top.label]
    elif main is None:
        visual_condition, observations = "Indeterminada", "Nenhum cacho identificado com confiança suficiente. Verifique o enquadramento da câmera."
        quality = "atencao"
    else:
        visual_condition, observations = "Boa", rng.choice(GOOD_OBSERVATIONS)

    return Analysis(
        variety_id=variety_id,
        confidence=round(confidence, 3),
        quality=quality,
        maturation=estimate_maturation(image_bytes, variety_type, main, rng),
        visual_condition=visual_condition,
        classification=CLASSIFICATION_BY_QUALITY[quality],
        observations=observations,
        clusters=clusters,
        anomalies=anomalies,
    )
