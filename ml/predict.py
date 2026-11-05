"""
Executa a análise em uma imagem com o MESMO código da API e mostra o resultado no formato da OASIS.

    python ml/predict.py caminho/para/uva.jpg --variety syrah            # modelo publicado (models/oasis-cachos.onnx)
    python ml/predict.py caminho/para/uva.jpg --variety syrah --color    # só a análise de cor (sem modelo)
    python ml/predict.py uva.jpg --variety syrah --model outro.onnx      # outro par .onnx + .json (ou pesos .pt)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import MATURATION_LABEL, QUALITY_LABEL, VARIETIES  # noqa: E402
from app.services.classifier import analyze  # noqa: E402
from app.services.detector import BUNDLED_MODEL, ColorDetector, load_yolo  # noqa: E402
from app.services.storage import load_image  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--model", default=str(BUNDLED_MODEL))
    p.add_argument("--confidence", type=float, default=None, help="limiar de confiança (padrão: o da ficha do modelo)")
    p.add_argument("--variety", required=True, choices=[v["id"] for v in VARIETIES], help="variedade cadastrada no talhão")
    p.add_argument("--color", action="store_true", help="usa a análise de cor em vez do modelo YOLO")
    args = p.parse_args()

    img = load_image(Path(args.image).read_bytes())
    variety_type = next(v["type"] for v in VARIETIES if v["id"] == args.variety)
    detector = ColorDetector() if args.color else load_yolo(Path(args.model), args.confidence)
    out = detector.detect(img, args.variety, variety_type)
    a = analyze(out.detections, args.variety, variety_type, img)
    print(f"Detector:          {out.model_version}")
    print(f"Variedade:         {a.variety_id} {'(cadastro informado em --variety)' if args.color or getattr(detector, 'clusters_only', False) else ''}")
    print(f"Cachos detectados: {len(a.clusters)}")
    print(f"Confiança:         {a.confidence:.0%}")
    print(f"Condição visual:   {a.visual_condition}")
    print(f"Qualidade:         {QUALITY_LABEL[a.quality]}")
    print(f"Maturação:         {MATURATION_LABEL[a.maturation]}")
    print(f"Classificação:     {a.classification}")
    for note in out.notes:
        print(f"Observação:        {note}")
    print(json.dumps([d.__dict__ for d in out.detections], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
