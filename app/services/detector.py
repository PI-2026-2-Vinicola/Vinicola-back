"""
Detectores de visão computacional.

Todos os detectores devolvem a mesma estrutura — uma lista de `RawDetection`
no formato normalizado do YOLO — o que permite trocar a simulação pelo
modelo real apenas com `OSAIS_DETECTOR=yolo`.
"""

from __future__ import annotations

import hashlib
import io
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from ..catalog import MILD_ANOMALIES, MODEL_CLASSES, SEVERE_ANOMALIES, VARIETY_IDS, yolo_class
from ..config import get_settings


@dataclass
class RawDetection:
    label: str
    confidence: float
    # x, y (canto superior esquerdo), largura, altura — normalizados 0–1
    box: tuple[float, float, float, float]


@dataclass
class DetectorOutput:
    detections: list[RawDetection]
    model_version: str


class Detector(Protocol):
    name: str

    def detect(self, image_bytes: bytes, variety_hint: str | None = None) -> DetectorOutput: ...


def cluster_half_width(v: float) -> float:
    """Largura relativa do cacho na altura relativa v (0 = topo) — mesmo formato usado no frontend."""
    if v < 0.22:
        return 0.62 + v * 1.7
    return max(0.08, 1 - ((v - 0.22) / 0.78) * 0.9)


class MockDetector:
    """
    Detector simulado e determinístico (mesma imagem → mesmo resultado).
    Útil para demonstrar o fluxo completo sem um modelo treinado.
    """

    name = "mock"
    version = "YOLOv8n-osais v0.3 (simulado)"

    def detect(self, image_bytes: bytes, variety_hint: str | None = None) -> DetectorOutput:
        seed = int.from_bytes(hashlib.sha256(image_bytes).digest()[:8], "big")
        return self.simulate(random.Random(seed), variety_hint)

    def simulate(self, rng: random.Random, variety_hint: str | None = None, quality: str | None = None) -> DetectorOutput:
        variety = variety_hint if variety_hint in VARIETY_IDS else rng.choice(VARIETY_IDS)
        if quality is None:
            r = rng.random()
            quality = "boa" if r < 0.72 else "atencao" if r < 0.9 else "critica"
        conf = {"boa": rng.uniform(0.87, 0.985), "atencao": rng.uniform(0.79, 0.95), "critica": rng.uniform(0.71, 0.92)}[quality]
        w = rng.uniform(0.34, 0.4)
        cluster = (rng.uniform(0.29, 0.36), rng.uniform(0.13, 0.19), w, rng.uniform(0.6, 0.68))
        dets = [RawDetection(yolo_class(variety), round(conf, 3), tuple(round(v, 3) for v in cluster))]  # type: ignore[arg-type]
        if rng.random() < 0.22:
            dets.append(RawDetection(yolo_class(variety), round(max(0.6, conf - rng.uniform(0.05, 0.15)), 3), (round(rng.uniform(0.74, 0.8), 3), round(rng.uniform(0.28, 0.36), 3), 0.19, 0.42)))
        if quality != "boa":
            labels = MILD_ANOMALIES if quality == "atencao" else SEVERE_ANOMALIES
            count = rng.randint(1, 2) if quality == "atencao" else rng.randint(2, 3)
            main = rng.choice(labels)
            placed: list[tuple[float, float, float, float]] = []
            for i in range(count):
                for _ in range(24):
                    v = rng.uniform(0.14, 0.66)
                    half = cluster_half_width(v) * 0.5 * cluster[2] * 0.7
                    cx = cluster[0] + cluster[2] / 2 + rng.uniform(-half, half)
                    cy = cluster[1] + v * cluster[3]
                    bw, bh = rng.uniform(0.07, 0.1), rng.uniform(0.09, 0.13)
                    box = (round(cx - bw / 2, 3), round(cy - bh / 2, 3), round(bw, 3), round(bh, 3))
                    if all(abs(p[1] - box[1]) > 0.1 or abs(p[0] - box[0]) > 0.24 for p in placed):
                        placed.append(box)
                        dets.append(RawDetection(main if i == 0 else rng.choice(labels), round(rng.uniform(0.58, 0.9), 3), box))
                        break
        return DetectorOutput(dets, self.version)


class YoloDetector:
    """
    Detector real com Ultralytics YOLO.

    O modelo deve ser treinado com as classes de `app.catalog.MODEL_CLASSES`
    (variedades + anomalias) — veja `ml/README.md`.
    """

    name = "yolo"

    def __init__(self, model_path: str, confidence: float = 0.35):
        try:
            from ultralytics import YOLO  # import tardio: a API funciona sem ultralytics instalado
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Instale as dependências de ML: pip install -r requirements-ml.txt") from exc
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.version = f"YOLO · {model_path.rsplit('/', 1)[-1]}"
        unknown = set(self.model.names.values()) - set(MODEL_CLASSES)
        if unknown:  # pragma: no cover
            print(f"[OSAIS] Aviso: classes do modelo fora do catálogo: {sorted(unknown)}")

    def detect(self, image_bytes: bytes, variety_hint: str | None = None) -> DetectorOutput:  # pragma: no cover - requer modelo
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        result = self.model.predict(image, conf=self.confidence, verbose=False)[0]
        dets: list[RawDetection] = []
        for xywhn, conf, cls in zip(result.boxes.xywhn.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist()):
            cx, cy, w, h = xywhn
            dets.append(RawDetection(self.model.names[int(cls)], round(float(conf), 3), (round(cx - w / 2, 4), round(cy - h / 2, 4), round(w, 4), round(h, 4))))
        return DetectorOutput(dets, self.version)


@lru_cache
def get_detector() -> Detector:
    settings = get_settings()
    if settings.osais_detector.lower() == "yolo":
        return YoloDetector(settings.osais_model_path, settings.osais_model_confidence)
    return MockDetector()
