"""
Métricas de detecção (estilo COCO) calculadas sobre a saída do pipeline de inferência da API.

Caixas no formato OASIS: (x, y, largura, altura) normalizados, com (x, y) no canto superior esquerdo.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np

Box = tuple[float, float, float, float]
IOU_THRESHOLDS = np.round(np.linspace(0.5, 0.95, 10), 2)


def iou_xywh(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _iou_matrix(preds: Sequence[Box], gts: Sequence[Box]) -> np.ndarray:
    return np.array([[iou_xywh(p, g) for g in gts] for p in preds], dtype=np.float64).reshape(len(preds), len(gts))


def match(preds: Sequence[tuple[float, Box]], gts: Sequence[Box], iou_thresholds: Iterable[float] = IOU_THRESHOLDS) -> np.ndarray:
    """
    Para cada predição (ordenadas por confiança) e cada limiar de IoU, 1 se ela casa com um cacho real
    ainda não casado (o de maior IoU), senão 0. Forma: (predições, limiares).
    """
    thresholds = list(iou_thresholds)
    order = sorted(range(len(preds)), key=lambda i: -preds[i][0])
    ious = _iou_matrix([preds[i][1] for i in order], gts)
    tp = np.zeros((len(preds), len(thresholds)), dtype=np.int8)
    for t_i, t in enumerate(thresholds):
        taken = np.zeros(len(gts), dtype=bool)
        for row in range(len(order)):
            if not len(gts):
                break
            cand = np.where(~taken & (ious[row] >= t), ious[row], -1)
            j = int(cand.argmax())
            if cand[j] >= 0:
                taken[j] = True
                tp[order[row], t_i] = 1
    return tp


def average_precision(recall: np.ndarray, precision: np.ndarray) -> float:
    """AP com interpolação de 101 pontos (COCO), a partir da curva precisão × recall."""
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0]))
    mpre = np.flip(np.maximum.accumulate(np.flip(mpre)))
    x = np.linspace(0, 1, 101)
    return float(np.trapezoid(np.interp(x, mrec, mpre), x))


def evaluate(images: Sequence[tuple[Sequence[tuple[float, Box]], Sequence[Box]]], confidence: float | None = None) -> dict:
    """
    `images`: para cada imagem, (predições [(confiança, caixa)], cachos reais [caixa]).
    Devolve mAP50, mAP50-95 e precisão/recall/F1 no limiar `confidence` (ou no de maior F1, se None).
    """
    scores, tps = [], []
    n_gt = 0
    for preds, gts in images:
        n_gt += len(gts)
        if preds:
            tps.append(match(preds, gts))
            scores += [p[0] for p in preds]
    if not scores or n_gt == 0:
        return {"instances": n_gt, "predictions": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "map50": 0.0, "map50_95": 0.0, "confidence": confidence or 0.0}
    scores_arr = np.array(scores)
    tp = np.concatenate(tps)
    order = np.argsort(-scores_arr, kind="stable")
    scores_arr, tp = scores_arr[order], tp[order]
    ctp = np.cumsum(tp, axis=0)
    cfp = np.cumsum(1 - tp, axis=0)
    recall = ctp / n_gt
    precision = ctp / np.maximum(ctp + cfp, 1)
    aps = [average_precision(recall[:, i], precision[:, i]) for i in range(tp.shape[1])]

    # precisão, recall e F1 (IoU 0,5) em cada limiar de confiança
    f1 = 2 * precision[:, 0] * recall[:, 0] / np.maximum(precision[:, 0] + recall[:, 0], 1e-9)
    if confidence is None:
        k = int(f1.argmax())
        confidence = float(scores_arr[k])
    k = int(np.searchsorted(-scores_arr, -confidence, side="right")) - 1
    if k < 0:
        p = r = f = 0.0
    else:
        p, r, f = float(precision[k, 0]), float(recall[k, 0]), float(f1[k])
    kept = int((scores_arr >= confidence).sum())
    tp50 = int(tp[:kept, 0].sum())
    return {
        "instances": n_gt,
        "predictions": kept,
        "true_positives": tp50,
        "false_positives": kept - tp50,
        "false_negatives": n_gt - tp50,
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f, 4),
        "map50": round(aps[0], 4),
        "map50_95": round(float(np.mean(aps)), 4),
        "confidence": round(float(confidence), 4),
    }
