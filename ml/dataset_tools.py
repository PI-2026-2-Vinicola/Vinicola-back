"""
Funções do preparo do dataset (validação, deduplicação, qualidade, divisão e formato YOLO).

Só dependem de NumPy e Pillow — rodam no mesmo ambiente da API e são cobertas por testes.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Caixa menor que isto (fração da largura/altura da imagem) é descartada como anotação degenerada.
MIN_BOX_SIDE = 0.002


@dataclass
class LabelCheck:
    boxes: list[tuple[int, float, float, float, float]]
    issues: list[str]
    clipped: int = 0
    dropped: int = 0


def parse_yolo_labels(text: str, num_classes: int = 1) -> LabelCheck:
    """
    Lê um arquivo de rótulos YOLO (`classe cx cy w h`, normalizados) e valida cada linha.
    Caixas que passam da borda são recortadas; linhas malformadas ou degeneradas são descartadas
    e registradas em `issues` — nunca corrigidas "no chute".
    """
    out = LabelCheck([], [])
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            out.issues.append(f"linha {n}: {len(parts)} campos (esperado 5)")
            out.dropped += 1
            continue
        try:
            cls = int(parts[0])
            cx, cy, w, h = (float(v) for v in parts[1:])
        except ValueError:
            out.issues.append(f"linha {n}: valor não numérico")
            out.dropped += 1
            continue
        if not 0 <= cls < num_classes:
            out.issues.append(f"linha {n}: classe {cls} fora do intervalo")
            out.dropped += 1
            continue
        box = clip_box(cx, cy, w, h)
        if box is None:
            out.issues.append(f"linha {n}: caixa degenerada ou fora da imagem")
            out.dropped += 1
            continue
        if box != (cx, cy, w, h):
            out.clipped += 1
        out.boxes.append((cls, *box))
    return out


def clip_box(cx: float, cy: float, w: float, h: float) -> tuple[float, float, float, float] | None:
    """Recorta a caixa (centro, largura, altura normalizados) aos limites da imagem."""
    if not all(np.isfinite(v) for v in (cx, cy, w, h)) or w <= 0 or h <= 0:
        return None
    x0, y0 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
    x1, y1 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
    if x1 - x0 < MIN_BOX_SIDE or y1 - y0 < MIN_BOX_SIDE:
        return None
    if (x0, y0, x1, y1) == (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2):
        return (cx, cy, w, h)
    return (round((x0 + x1) / 2, 6), round((y0 + y1) / 2, 6), round(x1 - x0, 6), round(y1 - y0, 6))


def format_yolo_labels(boxes: list[tuple[int, float, float, float, float]]) -> str:
    return "".join(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for c, cx, cy, w, h in boxes)


# ------------------------------------------------------------------ integridade e duplicatas
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img: Image.Image, size: int = 8) -> int:
    """Hash perceptual por diferença (64 bits): imagens quase iguais têm hashes próximos."""
    gray = np.asarray(img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (gray[:, 1:] > gray[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def near_duplicate_groups(hashes: dict[str, int], threshold: int) -> list[list[str]]:
    """Grupos (união-busca) de imagens cujo dHash difere em até `threshold` bits."""
    ids = sorted(hashes)
    parent = {i: i for i in ids}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a_i, a in enumerate(ids):
        for b in ids[a_i + 1 :]:
            if hamming(hashes[a], hashes[b]) <= threshold:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: g[0])


# ------------------------------------------------------------------ qualidade da imagem
def image_quality(img: Image.Image) -> dict[str, float]:
    """
    Indicadores simples e explicáveis: brilho e contraste (0–255) e nitidez (variância do
    laplaciano na imagem reduzida — valores baixos indicam foto desfocada ou tremida).
    """
    small = img.convert("L")
    small.thumbnail((512, 512))
    g = np.asarray(small, dtype=np.float32)
    lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
    return {"brightness": round(float(g.mean()), 2), "contrast": round(float(g.std()), 2), "sharpness": round(float(lap.var()), 2)}


def quality_flags(q: dict[str, float], *, dark: float = 35, bright: float = 225, low_contrast: float = 18, blurry: float = 15) -> list[str]:
    flags = []
    if q["brightness"] < dark:
        flags.append("escura")
    if q["brightness"] > bright:
        flags.append("superexposta")
    if q["contrast"] < low_contrast:
        flags.append("baixo_contraste")
    if q["sharpness"] < blurry:
        flags.append("desfocada")
    return flags


def resize_max_side(img: Image.Image, max_side: int) -> Image.Image:
    """Reduz mantendo a proporção (as caixas normalizadas continuam válidas). Nunca amplia."""
    if max(img.size) <= max_side:
        return img.copy()
    scale = max_side / max(img.size)
    return img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)


# ------------------------------------------------------------------ divisão treino/validação
def stratified_holdout(ids: list[str], key, fraction: float, seed: int, groups: list[list[str]] | None = None) -> set[str]:
    """
    Separa `fraction` dos ids para validação, estratificando por `key(id)` (a variedade) e mantendo
    juntos os grupos de quase-duplicatas (uma foto e sua "quase cópia" nunca ficam em lados diferentes).
    Determinístico para a mesma semente.
    """
    group_of: dict[str, tuple[str, ...]] = {}
    for g in groups or []:
        members = tuple(sorted(i for i in g if i in ids))
        for i in members:
            group_of[i] = members
    units: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    seen: set[str] = set()
    for i in sorted(ids):
        if i in seen:
            continue
        unit = group_of.get(i, (i,))
        seen.update(unit)
        units[key(i)].append(unit)
    rng = random.Random(seed)
    chosen: set[str] = set()
    for k in sorted(units):
        bucket = units[k]
        rng.shuffle(bucket)
        target = round(sum(len(u) for u in bucket) * fraction)
        taken = 0
        for unit in bucket:
            if taken >= target:
                break
            chosen.update(unit)
            taken += len(unit)
    return chosen


def write_data_yaml(path: Path, root: Path, names: list[str], header: list[str] | None = None) -> None:
    """data.yaml no formato do Ultralytics (caminho absoluto: funciona de qualquer diretório)."""
    lines = [f"# {h}" for h in header or []]
    lines += [f"path: {root.resolve().as_posix()}", "train: images/train", "val: images/val", "test: images/test", "", f"nc: {len(names)}", "names:"]
    lines += [f"  {i}: {n}" for i, n in enumerate(names)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
