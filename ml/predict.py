"""
Executa a análise em uma imagem e mostra o resultado no formato da OASIS.

    python ml/predict.py caminho/para/uva.jpg --variety syrah            # modelo YOLO (models/oasis-grapes.pt)
    python ml/predict.py caminho/para/uva.jpg --variety syrah --color    # análise de cor (sem modelo)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import MATURATION_LABEL, QUALITY_LABEL, VARIETIES  # noqa: E402
from app.services.classifier import analyze  # noqa: E402
from app.services.detector import ColorDetector, YoloDetector  # noqa: E402
from app.services.storage import load_image  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--model", default=str(ROOT / "models" / "oasis-grapes.pt"))
    p.add_argument("--variety", required=True, choices=[v["id"] for v in VARIETIES], help="variedade cadastrada no talhão")
    p.add_argument("--color", action="store_true", help="usa a análise de cor em vez do modelo YOLO")
    args = p.parse_args()

    img = load_image(Path(args.image).read_bytes())
    variety_type = next(v["type"] for v in VARIETIES if v["id"] == args.variety)
    detector = ColorDetector() if args.color else YoloDetector(args.model)
    out = detector.detect(img, args.variety, variety_type)
    a = analyze(out.detections, args.variety, variety_type, img)
    print(f"Detector:          {out.model_version}")
    print(f"Uva identificada:  {a.variety_id}")
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
