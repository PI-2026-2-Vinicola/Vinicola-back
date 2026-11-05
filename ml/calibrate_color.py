"""
Calibração da análise de cor com cachos REAIS do Embrapa WGISD.

    python ml/calibrate_color.py                 # usa ml/datasets/wgisd-cachos
    python ml/calibrate_color.py --target-rate 0.02

O WGISD não tem rótulos de defeitos (podridão, manchas, maturação desigual): é um dataset de
localização de cachos. Por isso a calibração mede apenas a ESPECIFICIDADE das regras — com que
frequência cada regra dispara em cachos reais de campo sem defeito anotado — e escolhe limiares que
disparam em no máximo `--target-rate` dos cachos de calibração (treino + validação). A SENSIBILIDADE
(acertar defeitos reais) não pode ser medida sem imagens anotadas de cachos com defeito.

Também compara a localização do cacho pela análise de cor (sem modelo) com as caixas reais, para
quantificar o ganho do detector YOLO.

Saída: ml/reports/calibracao_cor.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.services.detector import DEFAULT_THRESHOLDS, ColorDetector, ColorThresholds, berry_masks, region_color_shares  # noqa: E402
from ml.metrics import iou_xywh  # noqa: E402

RULES = {"brown_mild": "brown", "brown_severe": "brown", "mold": "mold", "off_color": "off_color"}


def load_split(dataset: Path, split: str, meta: dict[str, dict]):
    for img_path in sorted((dataset / "images" / split).glob("*.jpg")):
        boxes = []
        for line in (dataset / "labels" / split / f"{img_path.stem}.txt").read_text().splitlines():
            _, cx, cy, w, h = line.split()
            cx, cy, w, h = float(cx), float(cy), float(w), float(h)
            boxes.append((cx - w / 2, cy - h / 2, w, h))
        yield img_path, boxes, meta[img_path.stem]["tipo"]


def flag_rates(samples: list[dict], t: ColorThresholds) -> dict[str, float]:
    n = max(1, len(samples))
    any_flag = sum(1 for s in samples if s["brown"] >= t.brown_mild or s["mold"] >= t.mold or s["off_color"] >= t.off_color)
    return {
        "cachos": len(samples),
        "com_algum_achado": round(any_flag / n, 4),
        "mancha_leve_ou_podridao_por_manchas": round(sum(s["brown"] >= t.brown_mild for s in samples) / n, 4),
        "podridao_por_manchas_extensas": round(sum(s["brown"] >= t.brown_severe for s in samples) / n, 4),
        "podridao_por_mofo": round(sum(s["mold"] >= t.mold for s in samples) / n, 4),
        "maturacao_desigual": round(sum(s["off_color"] >= t.off_color for s in samples) / n, 4),
    }


def berry_mask_check(source: Path, meta: dict[str, dict], side: int = 512) -> dict:
    """
    Usa as máscaras de pixels do WGISD (cachos com segmentação) para medir a máscara de "cor de baga"
    da análise: que fração dos pixels reais de uva ela reconhece e quanto marca no fundo das caixas.
    Também registra a faixa HSV real dos pixels de uva, base para uma futura recalibração da cor.
    """
    stats: dict[str, dict[str, list]] = {t: {"cobertura": [], "fundo": [], "H": [], "S": [], "V": []} for t in ("Tinta", "Branca")}
    for npz in sorted((source / "data").glob("*.npz")):
        image_id = npz.stem
        if image_id not in meta:
            continue
        vtype = meta[image_id]["tipo"]
        img = Image.open(source / "data" / f"{image_id}.jpg").convert("RGB")
        masks = np.load(npz)["arr_0"]  # (altura, largura, cachos) — somente arrays, sem pickle
        grape = masks.any(axis=2)
        img.thumbnail((side, side))
        g = np.asarray(Image.fromarray(grape.astype(np.uint8) * 255).resize(img.size, Image.Resampling.NEAREST)) > 0
        boxes = np.zeros_like(g)
        ys, xs = np.nonzero(g)
        for k in range(masks.shape[2]):
            m = np.asarray(Image.fromarray(masks[:, :, k].astype(np.uint8) * 255).resize(img.size, Image.Resampling.NEAREST)) > 0
            yy, xx = np.nonzero(m)
            if yy.size:
                boxes[yy.min(): yy.max() + 1, xx.min(): xx.max() + 1] = True
        hsv = np.asarray(img.convert("HSV"), dtype=np.int16)
        berry, _ = berry_masks(hsv[..., 0], hsv[..., 1], hsv[..., 2], vtype)
        background = boxes & ~g
        if g.any():
            stats[vtype]["cobertura"].append(float(berry[g].mean()))
            sample = hsv[g][:: max(1, int(g.sum()) // 2000)]
            for i, ch in enumerate("HSV"):
                stats[vtype][ch] += sample[:, i].tolist()
        if background.any():
            stats[vtype]["fundo"].append(float(berry[background].mean()))
    out = {}
    for t, v in stats.items():
        if not v["cobertura"]:
            continue
        out[t] = {
            "imagens_com_mascara": len(v["cobertura"]),
            "pixels_de_uva_reconhecidos_mediana": round(float(np.median(v["cobertura"])), 4),
            "fundo_das_caixas_marcado_como_baga_mediana": round(float(np.median(v["fundo"])), 4) if v["fundo"] else None,
            "faixa_hsv_dos_pixels_de_uva_p5_p50_p95": {ch: [int(np.percentile(v[ch], q)) for q in (5, 50, 95)] for ch in "HSV"},
        }
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Calibração da análise de cor com os cachos reais do WGISD")
    p.add_argument("--dataset", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-cachos")
    p.add_argument("--target-rate", type=float, default=0.02, help="fração máxima de cachos sem defeito anotado em que cada regra pode disparar")
    p.add_argument("--source", type=Path, default=ROOT / "ml" / "datasets" / "fontes" / "wgisd", help="clone do WGISD (máscaras .npz)")
    p.add_argument("--out", type=Path, default=ROOT / "ml" / "reports" / "calibracao_cor.json")
    args = p.parse_args()

    with open(args.dataset / "metadata.csv", encoding="utf-8") as f:
        meta = {r["id"]: r for r in csv.DictReader(f)}

    samples: dict[str, list[dict]] = defaultdict(list)  # divisão → cachos
    color_loc: dict[str, list[float]] = defaultdict(list)  # melhor IoU da caixa da análise de cor com um cacho real
    color_flagged = defaultdict(int)
    images_per_split = defaultdict(int)
    detector = ColorDetector()
    for split in ("train", "val", "test"):
        for img_path, boxes, vtype in load_split(args.dataset, split, meta):
            img = Image.open(img_path).convert("RGB")
            images_per_split[split] += 1
            for box in boxes:
                res = region_color_shares(img, box, vtype)
                if res is not None:
                    samples[split].append({"tipo": vtype, **res[0]})
            out = detector.detect(img, None, vtype)
            clusters = [d for d in out.detections if d.label == "cacho"]
            color_loc[split].append(max((iou_xywh(clusters[0].box, b) for b in boxes), default=0.0) if clusters else 0.0)
            color_flagged[split] += any(d.label != "cacho" for d in out.detections)

    calib = samples["train"] + samples["val"]
    proposed = {}
    for rule, key in RULES.items():
        current = getattr(DEFAULT_THRESHOLDS, rule)
        q = float(np.quantile([s[key] for s in calib], 1 - args.target_rate))
        proposed[rule] = round(max(current, q), 4)
    if proposed["brown_severe"] <= proposed["brown_mild"]:
        proposed["brown_severe"] = round(proposed["brown_mild"] * 2, 4)
    proposed_t = ColorThresholds(**proposed)

    def percentiles(rows: list[dict], key: str) -> dict[str, float]:
        v = np.array([r[key] for r in rows]) if rows else np.zeros(1)
        return {f"p{q}": round(float(np.quantile(v, q / 100)), 4) for q in (50, 90, 95, 99)}

    report = {
        "dataset": str(args.dataset.relative_to(ROOT)) if args.dataset.is_relative_to(ROOT) else str(args.dataset),
        "observacao": (
            "O WGISD não anota defeitos: as taxas abaixo são disparos das regras em cachos reais sem defeito anotado "
            "(medida de especificidade). A sensibilidade a defeitos reais não pôde ser medida."
        ),
        "meta_de_disparo": args.target_rate,
        "limiares_anteriores": DEFAULT_THRESHOLDS.__dict__,
        "limiares_calibrados": proposed,
        "distribuicao_calibracao": {
            tipo: {k: percentiles([s for s in calib if s["tipo"] == tipo], k) for k in ("brown", "mold", "off_color", "berry")}
            for tipo in ("Tinta", "Branca")
        },
        "disparos_com_limiares_anteriores": {s: flag_rates(samples[s], DEFAULT_THRESHOLDS) for s in ("train", "val", "test")},
        "disparos_com_limiares_calibrados": {s: flag_rates(samples[s], proposed_t) for s in ("train", "val", "test")},
        "mascara_de_cor_de_baga_vs_pixels_reais": berry_mask_check(args.source, meta) if (args.source / "data").exists() else "clone do WGISD não encontrado (--source)",
        "analise_de_cor_sem_modelo": {
            s: {
                "imagens": images_per_split[s],
                "caixa_coincide_com_cacho_real_iou50": round(float(np.mean([v >= 0.5 for v in color_loc[s]])), 4),
                "iou_mediano_com_o_cacho_real_mais_proximo": round(float(np.median(color_loc[s])), 4),
                "imagens_com_anomalia_apontada": round(color_flagged[s] / max(1, images_per_split[s]), 4),
            }
            for s in ("train", "val", "test")
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("limiares_anteriores", "limiares_calibrados", "mascara_de_cor_de_baga_vs_pixels_reais", "analise_de_cor_sem_modelo")}, ensure_ascii=False, indent=1))
    print(f"Relatório: {args.out}")


if __name__ == "__main__":
    main()
