"""
Treinamento do modelo YOLO da OSAIS.

    pip install -r requirements-ml.txt
    python ml/train.py --epochs 100 --model yolov8n.pt

Ao final, os melhores pesos são copiados para models/osais-grapes.pt.
Para usá-los na API: OSAIS_DETECTOR=yolo OSAIS_MODEL_PATH=models/osais-grapes.pt
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import MODEL_CLASSES  # noqa: E402


def check_dataset_classes(yaml_path: Path) -> None:
    import yaml  # disponível com ultralytics

    names = yaml.safe_load(yaml_path.read_text())["names"]
    ordered = [names[i] for i in sorted(names)]
    if ordered != MODEL_CLASSES:
        raise SystemExit(f"As classes do dataset não batem com app/catalog.py:\n{ordered}\n!=\n{MODEL_CLASSES}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Treina o detector YOLO da OSAIS")
    parser.add_argument("--data", default=str(ROOT / "ml" / "dataset.yaml"))
    parser.add_argument("--model", default="yolov8n.pt", help="pesos iniciais (transfer learning)")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="ex.: 0 (GPU) ou cpu")
    args = parser.parse_args()

    from ultralytics import YOLO

    check_dataset_classes(Path(args.data))
    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(ROOT / "ml" / "runs"),
        name="osais-grapes",
        # Aumentos de dados coerentes com o campo: iluminação, ângulo e escala variam; cor não pode mudar muito
        hsv_h=0.005,
        hsv_s=0.4,
        hsv_v=0.4,
        degrees=10,
        scale=0.4,
        fliplr=0.5,
        mosaic=1.0,
        patience=25,
    )
    metrics = model.val()
    print(f"mAP50: {metrics.box.map50:.3f} · mAP50-95: {metrics.box.map:.3f}")

    best = Path(model.trainer.best)
    target = ROOT / "models" / "osais-grapes.pt"
    target.parent.mkdir(exist_ok=True)
    shutil.copy(best, target)
    print(f"Pesos copiados para {target}")


if __name__ == "__main__":
    main()
