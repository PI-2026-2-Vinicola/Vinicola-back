"""
Detectores de visão computacional.

Todos devolvem a mesma estrutura — caixas no formato normalizado do YOLO —, então
trocar a análise de cor pelo modelo treinado é só configuração (OASIS_DETECTOR).

- OnnxYoloDetector: modelo YOLO exportado para ONNX e executado com o ONNX Runtime (sem PyTorch na
  API). O modelo publicado em models/ foi treinado com fotos reais do Embrapa WGISD e localiza os
  cachos; dentro de cada cacho detectado, a análise de cor aponta manchas, mofo e bagas fora de cor.
  A variedade vem do cadastro do talhão (o modelo não identifica variedades). Ficha: model_card.py.
- ColorDetector: análise da imagem por segmentação de cor (usada quando não há modelo). Localiza o
  cacho pela cor e mede manchas acastanhadas, mofo acinzentado e bagas fora de cor.
- YoloDetector: pesos Ultralytics (.pt) com as classes de app.catalog.MODEL_CLASSES (requer ultralytics).
- MockDetector: gera detecções sintéticas; usado somente pelo comando explícito de
  dados de demonstração e pelos testes — nunca na ingestão de imagens.

Treinamento e inferência ficam separados: o pipeline de ml/ (PyTorch/Ultralytics) gera o par
`.onnx` + `.json`; a API só carrega esse par e roda a inferência.
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

from ..catalog import GENERIC_CLUSTER_CLASS, MILD_ANOMALIES, MODEL_CLASSES, SEVERE_ANOMALIES, VARIETY_IDS, yolo_class
from ..config import get_settings
from .model_card import ModelCard, load_model_card

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


@dataclass(frozen=True)
class ColorThresholds:
    """
    Fração da área do cacho a partir da qual cada achado de cor é apontado. Os valores padrão são
    os originais da análise de cor (ajustados com imagens sintéticas) e valem para o ColorDetector;
    os limiares calibrados com cachos reais do Embrapa WGISD (ml/calibrate_color.py) vêm na ficha
    do modelo publicado e são os usados pelo OnnxYoloDetector.
    """

    brown_mild: float = 0.02  # manchas acastanhadas → mancha_leve
    brown_severe: float = 0.06  # manchas acastanhadas extensas → podridao
    mold: float = 0.05  # cinza claro junto às bagas → podridao
    off_color: float = 0.10  # bagas avermelhadas em uva tinta → maturacao_desigual


DEFAULT_THRESHOLDS = ColorThresholds()


def color_masks(H: np.ndarray, S: np.ndarray, V: np.ndarray, variety_type: str) -> dict[str, np.ndarray]:
    """Máscaras de cor usadas pela análise (HSV do Pillow, escala 0–255)."""
    berry, off_color = berry_masks(H, S, V, variety_type)
    brown = (H >= 8) & (H <= 30) & (S >= 60) & (S <= 230) & (V >= 35) & (V <= 160)
    mold = (S < 30) & (V >= 150) & (V <= 235)
    return {"berry": berry, "off_color": off_color, "brown": brown, "mold": mold}


class ColorDetector:
    name = "color"
    version = "OASIS análise de cor v1"
    description = (
        "Análise da imagem por segmentação de cor (HSV): localiza o cacho, mede manchas acastanhadas, "
        "mofo acinzentado e bagas fora de cor. A variedade é a cadastrada para o talhão do sensor."
    )
    # Rótulos que esta análise pode produzir: o cacho (com a variedade do talhão) e três anomalias de cor.
    classes = [yolo_class(v) for v in VARIETY_IDS] + ["maturacao_desigual", "mancha_leve", "podridao"]

    def __init__(self, thresholds: ColorThresholds = DEFAULT_THRESHOLDS):
        self.thresholds = thresholds

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput:
        small = img.copy()
        small.thumbnail((480, 480))
        hsv = np.asarray(small.convert("HSV"), dtype=np.int16)
        H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]

        masks = color_masks(H, S, V, variety_type)
        berry, off_color, brown, mold = masks["berry"], masks["off_color"], masks["brown"], masks["mold"]
        t = self.thresholds

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
        anomaly(brown, "podridao" if brown_share >= t.brown_severe else "mancha_leve", t.brown_mild)
        anomaly(mold & (_dilate(grape_like)), "podridao", t.mold)
        anomaly(off_color, "maturacao_desigual", t.off_color)
        return DetectorOutput(detections, self.version, notes)


def _dilate(mask: np.ndarray, steps: int = 3) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        out[1:, :] |= out[:-1, :]
        out[:-1, :] |= out[1:, :]
        out[:, 1:] |= out[:, :-1]
        out[:, :-1] |= out[:, 1:]
    return out


# --------------------------------------------------------------------------- cor dentro de um cacho detectado
REGION_SIDE = 256  # lado máximo (px) do recorte do cacho analisado
MAX_CLUSTERS_ANALYZED = 12  # cachos (os de maior confiança) que passam pela análise de cor


def region_color_shares(img: Image.Image, box: tuple[float, float, float, float], variety_type: str) -> tuple[dict[str, float], dict[str, np.ndarray]] | None:
    """
    Frações da área da caixa do cacho com cada cor (manchas acastanhadas, mofo junto às bagas, bagas
    fora de cor e bagas na cor esperada) e as máscaras correspondentes, no recorte reduzido.
    Devolve None para caixas pequenas demais para uma análise confiável.
    """
    W, H = img.size
    x, y, w, h = box
    px0, py0 = int(x * W), int(y * H)
    px1, py1 = int((x + w) * W), int((y + h) * H)
    if px1 - px0 < 2 * GRID or py1 - py0 < 2 * GRID:
        return None
    crop = img.crop((px0, py0, px1, py1))
    crop.thumbnail((REGION_SIDE, REGION_SIDE))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.int16)
    m = color_masks(hsv[..., 0], hsv[..., 1], hsv[..., 2], variety_type)
    grape_like = m["berry"] | m["off_color"] | m["brown"]
    masks = {"berry": m["berry"], "brown": m["brown"], "mold": m["mold"] & _dilate(grape_like), "off_color": m["off_color"]}
    return {k: float(v.mean()) for k, v in masks.items()}, masks


def region_anomalies(img: Image.Image, box: tuple[float, float, float, float], variety_type: str, thresholds: ColorThresholds = DEFAULT_THRESHOLDS) -> list[RawDetection]:
    """Achados de cor dentro de um cacho já localizado (as caixas voltam em coordenadas da imagem)."""
    result = region_color_shares(img, box, variety_type)
    if result is None:
        return []
    shares, masks = result
    x, y, w, h = box
    found: list[RawDetection] = []

    def add(mask: np.ndarray, label: str, share: float) -> None:
        ratio = _cell_ratio(mask)
        rows, cols = ratio.shape
        if rows == 0 or cols == 0:
            return
        cells = _components(ratio > 0.25)
        if not cells:
            return
        bx, by, bw, bh = _bbox(max(cells, key=len), rows, cols)
        found.append(RawDetection(label, round(min(0.95, 0.5 + share * 4), 3), (round(x + bx * w, 4), round(y + by * h, 4), round(bw * w, 4), round(bh * h, 4))))

    if shares["brown"] >= thresholds.brown_mild:
        add(masks["brown"], "podridao" if shares["brown"] >= thresholds.brown_severe else "mancha_leve", shares["brown"])
    if shares["mold"] >= thresholds.mold:
        add(masks["mold"], "podridao", shares["mold"])
    if shares["off_color"] >= thresholds.off_color:
        add(masks["off_color"], "maturacao_desigual", shares["off_color"])
    return found


# --------------------------------------------------------------------------- YOLO em ONNX (ONNX Runtime)
def letterbox(img: Image.Image, size: int) -> tuple[np.ndarray, float, int, int]:
    """
    Redimensiona mantendo a proporção e completa com cinza (114) até size × size — o mesmo
    pré-processamento do Ultralytics. Devolve o tensor NCHW float32 (0–1), a escala e o deslocamento.
    """
    w, h = img.size
    r = min(size / w, size / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    left, top = int(round((size - nw) / 2 - 0.1)), int(round((size - nh) / 2 - 0.1))
    canvas.paste(img.convert("RGB").resize((nw, nh), Image.Resampling.BILINEAR), (left, top))
    tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(tensor), r, left, top


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Supressão de não máximos (caixas x1, y1, x2, y2)."""
    order = scores.argsort()[::-1]
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        overlap = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[overlap <= iou]
    return keep


def decode_yolo_output(
    output: np.ndarray, classes: list[str], *, ratio: float, left: int, top: int, image_size: tuple[int, int],
    confidence: float, iou: float, max_det: int = 300, end2end: bool | None = None,
) -> list[RawDetection]:
    """
    Saída do YOLO exportado → detecções OASIS (canto superior esquerdo, normalizadas pela imagem original).
    Aceita os dois formatos do Ultralytics: o bruto (1, 4 + classes, âncoras), que precisa de NMS, e o
    ponta a ponta (1, detecções, 6) = x1, y1, x2, y2, confiança, classe, já sem sobreposições.
    """
    pred = np.asarray(output, dtype=np.float32)[0]
    if pred.ndim != 2:
        raise ValueError(f"Saída do modelo com formato inesperado: {output.shape}")
    if end2end is None:
        end2end = pred.shape[1] == 6 and pred.shape[0] != 4 + len(classes)
    if end2end:
        if pred.shape[1] != 6:
            raise ValueError(f"Saída ponta a ponta com {pred.shape[1]} colunas (esperado 6).")
        boxes, scores, cls = pred[:, :4], pred[:, 4], pred[:, 5].astype(int)
        keep = np.flatnonzero(scores >= confidence)
    else:
        if pred.shape[0] == 4 + len(classes):
            pred = pred.T  # (âncoras, 4 + classes)
        if pred.shape[1] != 4 + len(classes):
            raise ValueError(f"A saída do modelo tem {pred.shape[1] - 4} classes; a ficha informa {len(classes)}.")
        cxcywh, class_scores = pred[:, :4], pred[:, 4:]
        cls = class_scores.argmax(axis=1)
        scores = class_scores.max(axis=1)
        boxes = np.stack([cxcywh[:, 0] - cxcywh[:, 2] / 2, cxcywh[:, 1] - cxcywh[:, 3] / 2, cxcywh[:, 0] + cxcywh[:, 2] / 2, cxcywh[:, 1] + cxcywh[:, 3] / 2], axis=1)
        candidates = np.flatnonzero(scores >= confidence)
        keep_list: list[int] = []
        for c in np.unique(cls[candidates]):
            idx = candidates[cls[candidates] == c]
            keep_list += [int(idx[k]) for k in nms(boxes[idx], scores[idx], iou)]
        keep = np.array(sorted(keep_list, key=lambda k: -scores[k]), dtype=int)
    W, H = image_size
    dets: list[RawDetection] = []
    for k in keep[np.argsort(-scores[keep])][:max_det] if keep.size else []:
        if not 0 <= cls[k] < len(classes):
            continue
        x0 = float(np.clip((boxes[k, 0] - left) / ratio, 0, W)) / W
        y0 = float(np.clip((boxes[k, 1] - top) / ratio, 0, H)) / H
        x1 = float(np.clip((boxes[k, 2] - left) / ratio, 0, W)) / W
        y1 = float(np.clip((boxes[k, 3] - top) / ratio, 0, H)) / H
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        dets.append(RawDetection(classes[int(cls[k])], round(float(scores[k]), 3), (round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4))))
    return dets


class OnnxYoloDetector:
    """
    Modelo YOLO exportado para ONNX (gerado por ml/export_model.py) com a sua ficha (.json).
    Na tarefa `deteccao_cachos`, o modelo localiza os cachos e a análise de cor examina cada um deles.
    """

    name = "yolo"

    def __init__(self, model_path: str | Path, confidence: float | None = None, thresholds: ColorThresholds = DEFAULT_THRESHOLDS):
        import onnxruntime as ort  # import tardio: sem onnxruntime, a API segue com a análise de cor

        self.path = Path(model_path)
        self.card: ModelCard = load_model_card(self.path)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(self.path), sess_options=opts, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        side = inp.shape[2] if len(inp.shape) == 4 and isinstance(inp.shape[2], int) else self.card.input_size
        self.input_size = int(side)
        self.confidence = confidence if confidence is not None else self.card.confidence_threshold
        cal = self.card.color_thresholds
        self.thresholds = ColorThresholds(cal.brown_mild, cal.brown_severe, cal.mold, cal.off_color) if cal and thresholds is DEFAULT_THRESHOLDS else thresholds
        self.clusters_only = self.card.task == "deteccao_cachos"
        if self.clusters_only:
            self.version = f"YOLO cachos · {self.card.name} {self.card.version} + {ColorDetector.version}"[:80]
            self.description = (
                f"Modelo {self.card.architecture} treinado com {self.card.dataset.images.get('total', 0)} fotos reais do "
                f"{self.card.dataset.name} ({self.card.dataset.institution}) localiza os cachos de uva; dentro de cada cacho, "
                "a análise de cor aponta manchas acastanhadas, mofo e bagas fora de cor. A variedade é a cadastrada no talhão."
            )
            self.classes = [GENERIC_CLUSTER_CLASS, "maturacao_desigual", "mancha_leve", "podridao"]
        else:
            self.version = f"YOLO · {self.card.name} {self.card.version}"[:80]
            self.description = f"Modelo {self.card.architecture} treinado com as classes de variedades e anomalias da OASIS."
            self.classes = list(self.card.classes)
        self.model_classes = list(self.card.classes)

    def predict(self, img: Image.Image) -> list[RawDetection]:
        tensor, ratio, left, top = letterbox(img, self.input_size)
        output = self.session.run(None, {self.input_name: tensor})[0]
        return decode_yolo_output(
            output, self.model_classes, ratio=ratio, left=left, top=top, image_size=img.size,
            confidence=self.confidence, iou=self.card.iou_threshold, end2end=self.card.end2end,
        )

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput:
        found = self.predict(img)
        if not self.clusters_only:
            return DetectorOutput([d for d in found if d.label in MODEL_CLASSES], self.version)
        label = yolo_class(variety_hint) if variety_hint else GENERIC_CLUSTER_CLASS
        clusters = [RawDetection(label, d.confidence, d.box) for d in found]
        anomalies: list[RawDetection] = []
        for c in clusters[:MAX_CLUSTERS_ANALYZED]:
            anomalies += region_anomalies(img, c.box, variety_type, self.thresholds)
        notes = [f"{len(clusters)} cacho(s) localizado(s) pelo modelo com confiança ≥ " + f"{self.confidence:.2f}.".replace(".", ",", 1)]
        if len(clusters) > MAX_CLUSTERS_ANALYZED:
            notes.append(f"Análise de cor feita nos {MAX_CLUSTERS_ANALYZED} cachos de maior confiança.")
        return DetectorOutput(clusters + anomalies, self.version, notes)


# --------------------------------------------------------------------------- YOLO
def model_classes(names: dict | list) -> list[str]:
    """Classes de um modelo Ultralytics (`model.names` é um dict índice → nome)."""
    return [str(n) for n in (names.values() if isinstance(names, dict) else names)]


def check_model_classes(classes: list[str], model_name: str) -> list[str]:
    """
    Garante que o modelo foi treinado para a OASIS. Um modelo genérico (ex.: COCO, com "person", "car"…)
    não tem nenhuma classe do catálogo e é recusado: suas saídas não podem virar variedade ou anomalia.
    Devolve as classes desconhecidas (ignoradas na inferência) de um modelo parcialmente compatível.
    """
    if classes == [GENERIC_CLUSTER_CLASS]:
        raise ValueError(f"{model_name} é um modelo de detecção de cachos: exporte-o com ml/export_model.py e use o par .onnx + .json.")
    unknown = [c for c in classes if c not in MODEL_CLASSES]
    if len(unknown) == len(classes):
        raise ValueError(f"O modelo {model_name} não contém nenhuma classe da OASIS (ml/dataset.yaml); parece ser um modelo genérico.")
    if unknown:
        log.warning("Classes do modelo %s fora do catálogo (serão ignoradas): %s", model_name, sorted(unknown))
    return unknown


def to_raw_detections(boxes_xywhn: list, confidences: list, class_ids: list, names: dict | list) -> tuple[list[RawDetection], list[str]]:
    """Caixas do Ultralytics (centro x, centro y, largura, altura — normalizados) → formato OASIS (canto superior esquerdo)."""
    dets: list[RawDetection] = []
    ignored: list[str] = []
    for (cx, cy, w, h), conf, cls in zip(boxes_xywhn, confidences, class_ids):
        label = str(names[int(cls)])  # dict índice → nome (Ultralytics) ou lista
        if label not in MODEL_CLASSES:
            ignored.append(label)
            continue
        # Recorta a caixa nas bordas da imagem (as restrições do banco exigem coordenadas entre 0 e 1).
        x0, y0 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
        x1, y1 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
        if x1 <= x0 or y1 <= y0:
            continue
        dets.append(RawDetection(label, round(float(conf), 3), (round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4))))
    return dets, ignored


class YoloDetector:
    name = "yolo"

    def __init__(self, model_path: str, confidence: float = 0.35):
        from ultralytics import YOLO  # import tardio: a API funciona sem ultralytics instalado

        self.model = YOLO(model_path)
        self.confidence = confidence
        self.version = f"YOLO · {Path(model_path).name}"
        self.description = f"Modelo YOLO treinado ({Path(model_path).name}) com as classes de variedades e anomalias."
        self.classes = model_classes(self.model.names)
        self.unknown_classes = check_model_classes(self.classes, Path(model_path).name)

    def detect(self, img: Image.Image, variety_hint: str | None, variety_type: str) -> DetectorOutput:  # pragma: no cover - requer modelo
        result = self.model.predict(img, conf=self.confidence, verbose=False)[0]
        dets, ignored = to_raw_detections(result.boxes.xywhn.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist(), self.model.names)
        notes = [f"Detecções de classes fora do catálogo ignoradas: {', '.join(sorted(set(ignored)))}"] if ignored else []
        return DetectorOutput(dets, self.version, notes)


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


# Modelo publicado no repositório (treinado com o Embrapa WGISD; ver ml/README.md).
BUNDLED_MODEL = Path(__file__).resolve().parents[2] / "models" / "oasis-cachos.onnx"


# Valor padrão de OASIS_MODEL_PATH antes do modelo publicado (ainda presente em arquivos .env antigos).
LEGACY_MODEL_PATHS = {"models/oasis-grapes.pt", "./models/oasis-grapes.pt"}


def resolve_model_path(configured: str) -> Path:
    """
    Arquivo de pesos a usar: o configurado em OASIS_MODEL_PATH. Se o .env ainda tiver o antigo valor
    padrão (models/oasis-grapes.pt) e esse arquivo não existir, usa o modelo publicado no repositório.
    Qualquer outro caminho configurado é respeitado como está.
    """
    path = Path(configured)
    if not path.exists() and configured.strip().replace("\\", "/") in LEGACY_MODEL_PATHS and BUNDLED_MODEL.exists():
        log.info("OASIS_MODEL_PATH=%s não existe; usando o modelo publicado em %s.", configured, BUNDLED_MODEL.relative_to(BUNDLED_MODEL.parents[1]))
        return BUNDLED_MODEL
    return path


def load_yolo(path: Path, confidence: float | None) -> Detector:
    if path.suffix.lower() == ".onnx":
        return OnnxYoloDetector(path, confidence)
    return YoloDetector(str(path), 0.35 if confidence is None else confidence)


@lru_cache
def get_detector() -> Detector:
    settings = get_settings()
    choice = settings.oasis_detector.lower()
    if choice == "color":
        return ColorDetector()
    path = resolve_model_path(settings.oasis_model_path)
    if path.exists():
        try:
            detector = load_yolo(path, settings.oasis_model_confidence)
            log.info("Detector em uso: %s (%s)", detector.version, path.name)
            return detector
        except Exception as exc:  # pragma: no cover - depende do ambiente
            if choice == "yolo":
                raise
            log.warning("Modelo YOLO indisponível (%s); usando análise de cor.", exc)
    elif choice == "yolo":
        raise RuntimeError(f"OASIS_DETECTOR=yolo, mas o arquivo de pesos não existe: {settings.oasis_model_path}")
    return ColorDetector()


__all__ = [
    "ColorDetector", "OnnxYoloDetector", "YoloDetector", "MockDetector", "RawDetection", "DetectorOutput", "get_detector", "VARIETY_IDS",
    "check_model_classes", "to_raw_detections", "decode_yolo_output", "letterbox", "region_anomalies", "ColorThresholds",
]
