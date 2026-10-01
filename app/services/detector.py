"""
Detectores de visão computacional.

Todos devolvem a mesma estrutura — caixas no formato normalizado do YOLO —, então
trocar a análise de cor pelo modelo treinado é só configuração (OASIS_DETECTOR).

- ColorDetector: análise real da imagem por segmentação de cor (padrão enquanto não
  existe um modelo treinado). Localiza o cacho, mede manchas acastanhadas, mofo
  acinzentado e bagas fora de cor. A variedade vem do cadastro do talhão do sensor.
- YoloDetector: modelo Ultralytics treinado com as classes de app.catalog.MODEL_CLASSES.
- MockDetector: gera detecções sintéticas; usado somente pelo comando explícito de
  dados de demonstração e pelos testes — nunca na ingestão de imagens.
"""

from __future__ import annotations

import logging
import random
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from ..catalog import MILD_ANOMALIES, MODEL_CLASSES, SEVERE_ANOMALIES, VARIETY_IDS, yolo_class
from ..config import get_settings

log = logging.getLogger("oasis.detector")


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
    notes: list[str] = field(default_factory=list)


class Detector(Protocol):
    name: str
    description: str

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput: ...


# --------------------------------------------------------------------------- análise de cor
GRID = 8  # tamanho da célula (px) na imagem reduzida


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Componentes conexos (vizinhança 4) de uma grade booleana pequena."""
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    rows, cols = mask.shape
    for r in range(rows):
        for c in range(cols):
            if mask[r, c] and not seen[r, c]:
                comp, queue = [], deque([(r, c)])
                seen[r, c] = True
                while queue:
                    y, x = queue.popleft()
                    comp.append((y, x))
                    for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                        if 0 <= ny < rows and 0 <= nx < cols and mask[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            queue.append((ny, nx))
                comps.append(comp)
    return comps


def _cell_ratio(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    rows, cols = h // GRID, w // GRID
    trimmed = mask[: rows * GRID, : cols * GRID]
    return trimmed.reshape(rows, GRID, cols, GRID).mean(axis=(1, 3))


def _bbox(cells: list[tuple[int, int]], rows: int, cols: int) -> tuple[float, float, float, float]:
    ys = [c[0] for c in cells]
    xs = [c[1] for c in cells]
    x0, x1 = min(xs) / cols, (max(xs) + 1) / cols
    y0, y1 = min(ys) / rows, (max(ys) + 1) / rows
    return (round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4))


def berry_masks(H: np.ndarray, S: np.ndarray, V: np.ndarray, variety_type: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Máscaras de bagas na cor esperada e fora de cor (HSV do Pillow, escala 0–255).
    Em uvas brancas, bagas verdes têm a mesma cor da folhagem — essa anomalia só é
    apontada pelo modelo YOLO treinado, não pela análise de cor.
    """
    if variety_type == "Branca":
        berry = (H >= 25) & (H <= 60) & (S >= 50) & (V >= 120)
        return berry, np.zeros_like(berry)
    berry = (((H >= 165) | (H <= 12)) & (S >= 35) & (V >= 15) & (V <= 150)) | ((H >= 140) & (H < 165) & (S >= 30) & (V <= 110))
    off_color = ((H >= 215) | (H <= 12)) & (S >= 60) & (V > 150)  # bagas avermelhadas (pintor)
    return berry, off_color


class ColorDetector:
    name = "color"
    version = "OASIS análise de cor v1"
    description = (
        "Análise da imagem por segmentação de cor (HSV): localiza o cacho, mede manchas acastanhadas, "
        "mofo acinzentado e bagas fora de cor. A variedade é a cadastrada para o talhão do sensor."
    )

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput:
        small = img.copy()
        small.thumbnail((480, 480))
        hsv = np.asarray(small.convert("HSV"), dtype=np.int16)
        H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]

        berry, off_color = berry_masks(H, S, V, variety_type)
        brown = (H >= 8) & (H <= 30) & (S >= 60) & (S <= 230) & (V >= 35) & (V <= 160)
        mold = (S < 30) & (V >= 150) & (V <= 235)

        grape_like = berry | off_color | brown
        ratio = _cell_ratio(grape_like)
        rows, cols = ratio.shape
        comps = _components(ratio > 0.3)
        notes: list[str] = []
        if not comps:
            return DetectorOutput([], self.version, ["Nenhuma região com cor de uva encontrada na imagem."])
        main = max(comps, key=len)
        if len(main) < 0.012 * rows * cols:
            return DetectorOutput([], self.version, ["Região com cor de uva pequena demais para identificar um cacho."])

        box = _bbox(main, rows, cols)
        px0, py0 = int(box[0] * cols) * GRID, int(box[1] * rows) * GRID
        px1, py1 = int((box[0] + box[2]) * cols) * GRID, int((box[1] + box[3]) * rows) * GRID
        region = (slice(py0, py1), slice(px0, px1))
        area = max(1, (py1 - py0) * (px1 - px0))
        density = float(grape_like[region].mean())
        confidence = round(min(0.97, 0.5 + 0.55 * density), 3)
        detections = [RawDetection(yolo_class(variety_hint) if variety_hint else "cacho", confidence, box)]

        def anomaly(mask: np.ndarray, label: str, min_ratio: float) -> None:
            part = np.zeros_like(mask)
            part[region] = mask[region]
            share = float(part.sum()) / area
            if share < min_ratio:
                return
            cells = _components(_cell_ratio(part) > 0.25)
            if not cells:
                return
            biggest = max(cells, key=len)
            detections.append(RawDetection(label, round(min(0.95, 0.5 + share * 4), 3), _bbox(biggest, rows, cols)))
            notes.append(f"{label}: {share:.1%} da área do cacho")

        brown_share = float(brown[region].sum()) / area
        anomaly(brown, "podridao" if brown_share >= 0.06 else "mancha_leve", 0.02)
        anomaly(mold & (_dilate(grape_like)), "podridao", 0.05)
        anomaly(off_color, "maturacao_desigual", 0.10)
        return DetectorOutput(detections, self.version, notes)


def _dilate(mask: np.ndarray, steps: int = 3) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        out[1:, :] |= out[:-1, :]
        out[:-1, :] |= out[1:, :]
        out[:, 1:] |= out[:, :-1]
        out[:, :-1] |= out[:, 1:]
    return out


# --------------------------------------------------------------------------- YOLO
class YoloDetector:
    name = "yolo"

    def __init__(self, model_path: str, confidence: float = 0.35):
        from ultralytics import YOLO  # import tardio: a API funciona sem ultralytics instalado

        self.model = YOLO(model_path)
        self.confidence = confidence
        self.version = f"YOLO · {Path(model_path).name}"
        self.description = f"Modelo YOLO treinado ({Path(model_path).name}) com as classes de variedades e anomalias."
        unknown = set(self.model.names.values()) - set(MODEL_CLASSES)
        if unknown:  # pragma: no cover
            log.warning("Classes do modelo fora do catálogo: %s", sorted(unknown))

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput:  # pragma: no cover - requer modelo
        result = self.model.predict(img, conf=self.confidence, verbose=False)[0]
        dets: list[RawDetection] = []
        for xywhn, conf, cls in zip(result.boxes.xywhn.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist()):
            cx, cy, w, h = xywhn
            x, y = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
            w, h = min(w, 1.0 - x), min(h, 1.0 - y)
            dets.append(RawDetection(self.model.names[int(cls)], round(float(conf), 3), (round(x, 4), round(y, 4), round(w, 4), round(h, 4))))
        return DetectorOutput(dets, self.version)


# --------------------------------------------------------------------------- demonstração
def cluster_half_width(v: float) -> float:
    if v < 0.22:
        return 0.62 + v * 1.7
    return max(0.08, 1 - ((v - 0.22) / 0.78) * 0.9)


class MockDetector:
    """Detecções sintéticas — SOMENTE para o comando de dados de demonstração e testes."""

    name = "mock"
    version = "Demonstração (sintético)"
    description = "Detecções sintéticas para demonstração. Não analisa imagens."

    def simulate(self, rng: random.Random, variety: str, quality: str) -> DetectorOutput:
        conf = {"boa": rng.uniform(0.87, 0.985), "atencao": rng.uniform(0.79, 0.95), "critica": rng.uniform(0.71, 0.92)}[quality]
        w = rng.uniform(0.34, 0.4)
        cluster = (rng.uniform(0.29, 0.36), rng.uniform(0.13, 0.19), w, rng.uniform(0.6, 0.68))
        dets = [RawDetection(yolo_class(variety), round(conf, 3), tuple(round(v, 3) for v in cluster))]  # type: ignore[arg-type]
        if quality != "boa":
            labels = MILD_ANOMALIES if quality == "atencao" else SEVERE_ANOMALIES
            for i in range(1 if quality == "atencao" else 2):
                v = rng.uniform(0.15 + i * 0.25, 0.35 + i * 0.25)
                half = cluster_half_width(v) * 0.5 * cluster[2] * 0.6
                cx = cluster[0] + cluster[2] / 2 + rng.uniform(-half, half)
                cy = cluster[1] + v * cluster[3]
                dets.append(RawDetection(rng.choice(labels), round(rng.uniform(0.6, 0.9), 3), (round(cx - 0.04, 3), round(cy - 0.05, 3), 0.08, 0.1)))
        return DetectorOutput(dets, self.version)


@lru_cache
def get_detector() -> Detector:
    settings = get_settings()
    choice = settings.oasis_detector.lower()
    if choice in ("yolo", "auto") and Path(settings.oasis_model_path).exists():
        try:
            return YoloDetector(settings.oasis_model_path, settings.oasis_model_confidence)
        except Exception as exc:  # pragma: no cover - depende do ambiente
            if choice == "yolo":
                raise
            log.warning("Modelo YOLO indisponível (%s); usando análise de cor.", exc)
    elif choice == "yolo":
        raise RuntimeError(f"OASIS_DETECTOR=yolo, mas o arquivo de pesos não existe: {settings.oasis_model_path}")
    return ColorDetector()


__all__ = ["ColorDetector", "YoloDetector", "MockDetector", "RawDetection", "DetectorOutput", "get_detector", "VARIETY_IDS"]
