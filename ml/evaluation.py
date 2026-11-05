"""
Avaliação do modelo publicado com o MESMO código de inferência da API (app/services/detector.py).

As métricas da ficha do modelo vêm daqui — medem o que a aplicação realmente executa (ONNX Runtime,
pré-processamento letterbox e pós-processamento da API), e não apenas o modelo dentro do PyTorch.
"""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image

from ml.metrics import evaluate

Item = tuple[str, list[tuple[float, tuple[float, float, float, float]]], list[tuple[float, float, float, float]]]


def read_ground_truth(label_path: Path) -> list[tuple[float, float, float, float]]:
    boxes = []
    for line in label_path.read_text().splitlines():
        if line.strip():
            _, cx, cy, w, h = line.split()
            cx, cy, w, h = float(cx), float(cy), float(w), float(h)
            boxes.append((cx - w / 2, cy - h / 2, w, h))
    return boxes


def run_split(detector, dataset: Path, split: str) -> tuple[list[Item], list[float]]:
    """Predições (com confiança mínima do detector) e cachos reais de cada imagem da divisão, mais o tempo de cada inferência."""
    items: list[Item] = []
    times: list[float] = []
    for img_path in sorted((dataset / "images" / split).glob("*.jpg")):
        img = Image.open(img_path).convert("RGB")
        start = time.perf_counter()
        preds = detector.predict(img)
        times.append((time.perf_counter() - start) * 1000)
        gts = read_ground_truth(dataset / "labels" / split / f"{img_path.stem}.txt")
        items.append((img_path.stem, [(d.confidence, d.box) for d in preds], gts))
    return items, times


def metrics_for(items: list[Item], confidence: float | None) -> dict:
    return evaluate([(p, g) for _, p, g in items], confidence)


def per_variety(items: list[Item], dataset: Path, confidence: float) -> dict[str, dict]:
    with open(dataset / "metadata.csv", encoding="utf-8") as f:
        variety = {r["id"]: r["variedade"] for r in csv.DictReader(f)}
    groups: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        groups[variety.get(item[0], "?")].append(item)
    return {v: {"images": len(g), **metrics_for(g, confidence)} for v, g in sorted(groups.items())}
