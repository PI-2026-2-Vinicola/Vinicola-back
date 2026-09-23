"""
Executa o modelo em uma imagem e mostra o resultado no formato da OSAIS.

    python ml/predict.py caminho/para/uva.jpg [--variety cabernet-sauvignon] [--mock]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import MATURATIONS, QUALITY_LABEL  # noqa: E402
from app.services.classifier import analyze  # noqa: E402
from app.services.detector import MockDetector, YoloDetector  # noqa: E402

MATURATION_LABEL = dict(zip(MATURATIONS, ["Em desenvolvimento", "Pintor (véraison)", "Em maturação", "Adequada", "Sobrematuração"]))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("--model", default=str(ROOT / "models" / "osais-grapes.pt"))
    p.add_argument("--variety", default=None, help="variedade esperada do talhão (dica)")
    p.add_argument("--mock", action="store_true", help="usa o detector simulado")
    args = p.parse_args()

    data = Path(args.image).read_bytes()
    detector = MockDetector() if args.mock else YoloDetector(args.model)
    out = detector.detect(data, args.variety)
    a = analyze(out.detections, args.variety, image_bytes=data)
    print(f"Uva identificada:  {a.variety_id}")
    print(f"Confiança:         {a.confidence:.0%}")
    print(f"Condição visual:   {a.visual_condition}")
    print(f"Qualidade:         {QUALITY_LABEL[a.quality]}")
    print(f"Maturação:         {MATURATION_LABEL[a.maturation]}")
    print(f"Classificação:     {a.classification}")
    print(json.dumps([d.__dict__ for d in out.detections], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
