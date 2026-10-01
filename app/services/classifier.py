"""
Regras que transformam as detecções em informação para o produtor:
variedade, qualidade, condição visual, maturação, classificação e observações.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from PIL import Image

from ..catalog import ANOMALY_TEXT, CLASSIFICATION_BY_QUALITY, GOOD_OBSERVATIONS, MATURATION_UNKNOWN, SEVERE_ANOMALIES, variety_from_class
from .detector import RawDetection, berry_masks

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


def estimate_maturation(img: Image.Image | None, variety_type: str, cluster: RawDetection | None) -> str:
    """
    Estágio de maturação pela cor das bagas dentro da caixa do cacho (somente pixels
    com cor de baga, para não misturar folhas ao fundo). Regra simples e explicável;
    sem imagem ou sem cacho identificado, o estágio fica "não informado".
    """
    if img is None or cluster is None:
        return MATURATION_UNKNOWN
    x, y, w, h = cluster.box
    W, H = img.size
    crop = img.crop((int(x * W), int(y * H), max(int((x + w) * W), int(x * W) + 1), max(int((y + h) * H), int(y * H) + 1)))
    crop.thumbnail((320, 320))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.int16)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    berry, off_color = berry_masks(hue, sat, val, variety_type)
    n_berry, n_off = int(berry.sum()), int(off_color.sum())
    if n_berry + n_off < 0.05 * berry.size:
        return MATURATION_UNKNOWN
    if variety_type == "Tinta":
        if n_off > n_berry:
            return "pintor"  # predominância de bagas avermelhadas (véraison)
        v = float(val[berry].mean())
        return "maturacao" if v > 95 else "adequada"
    h_mean = float(hue[berry].mean())
    if h_mean > 52:
        return "maturacao"  # amarelo-esverdeado
    return "adequada" if h_mean >= 30 else "sobrematuracao"  # dourado → âmbar


def analyze(
    detections: list[RawDetection],
    variety_hint: str | None,
    variety_type: str = "Tinta",
    img: Image.Image | None = None,
    seed: int = 0,
) -> Analysis:
    rng = random.Random(seed)
    clusters = sorted([d for d in detections if variety_from_class(d.label) or d.label == "cacho"], key=lambda d: d.confidence, reverse=True)
    anomalies = [d for d in detections if d.label in ANOMALY_TEXT and d.confidence >= ANOMALY_MIN_CONFIDENCE]

    main = clusters[0] if clusters else None
    variety_id = (variety_from_class(main.label) if main else None) or variety_hint or "cabernet-sauvignon"
    confidence = main.confidence if main else 0.0

    if any(a.label in SEVERE_ANOMALIES for a in anomalies):
        quality = "critica"
    elif anomalies:
        quality = "atencao"
    else:
        quality = "boa"

    if main is None:
        visual_condition = "Cacho não identificado"
        observations = "Nenhum cacho foi identificado com confiança suficiente. Verifique o enquadramento, a iluminação e a limpeza da lente."
        quality = "atencao"
    elif anomalies:
        top = max(anomalies, key=lambda a: (a.label in SEVERE_ANOMALIES, a.confidence))
        visual_condition, observations = ANOMALY_TEXT[top.label]
    else:
        visual_condition, observations = "Boa", rng.choice(GOOD_OBSERVATIONS)

    return Analysis(
        variety_id=variety_id,
        confidence=round(confidence, 3),
        quality=quality,
        maturation=estimate_maturation(img, variety_type, main),
        visual_condition=visual_condition,
        classification=CLASSIFICATION_BY_QUALITY[quality],
        observations=observations,
        clusters=clusters,
        anomalies=anomalies,
    )
