"""
Avalia um modelo publicado (.onnx + .json) num dataset no formato YOLO, com o MESMO código de
inferência da API — para comparar um modelo novo com o atual no mesmo conjunto de teste.

    python ml/evaluate.py                                                     # modelo publicado, teste do WGISD
    python ml/evaluate.py --model models/novo.onnx --data ml/datasets/oasis-campo-anotado --split test
    python ml/evaluate.py --confidence 0.4                                    # outro limiar

Imprime precisão, recall, F1 (no limiar da ficha ou no informado), mAP50, mAP50-95, a contagem de
verdadeiros/falsos positivos e falsos negativos (IoU 0,5), as métricas por variedade e o tempo de inferência.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.detector import BUNDLED_MODEL, OnnxYoloDetector  # noqa: E402
from ml.evaluation import metrics_for, per_variety, run_split  # noqa: E402


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description="Avalia um modelo ONNX da OASIS com o pipeline de inferência da API")
    p.add_argument("--model", type=Path, default=BUNDLED_MODEL)
    p.add_argument("--data", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-cachos", help="pasta do dataset (images/, labels/, metadata.csv)")
    p.add_argument("--split", default="test", choices=["train", "val", "test"])
    p.add_argument("--confidence", type=float, default=None, help="limiar para P/R/F1 (padrão: o da ficha do modelo)")
    p.add_argument("--out", type=Path, help="grava o resultado em JSON")
    args = p.parse_args(argv)

    detector = OnnxYoloDetector(args.model, confidence=0.001)  # todas as detecções; o limiar entra no cálculo
    threshold = args.confidence if args.confidence is not None else detector.card.confidence_threshold
    items, times = run_split(detector, args.data, args.split)
    result = {
        "modelo": f"{detector.card.name} {detector.card.version} ({args.model.name})",
        "dataset": str(args.data),
        "divisao": args.split,
        "imagens": len(items),
        "metricas": metrics_for(items, threshold),
        "tempo_inferencia_ms": {"mediana": round(statistics.median(times), 1), "max": round(max(times), 1)} if times else None,
    }
    if (args.data / "metadata.csv").exists():
        result["por_variedade"] = per_variety(items, args.data, threshold)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return result


if __name__ == "__main__":
    main()
