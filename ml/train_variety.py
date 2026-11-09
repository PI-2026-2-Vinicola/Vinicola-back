"""
ETAPA 2 — TREINAMENTO do classificador YOLO de VARIEDADES (recortes dos cachos).

    python ml/prepare_variety_dataset.py
    python ml/train_variety.py                      # transfer learning a partir de yolo26n-cls.pt (ImageNet)

O classificador recebe o recorte de UM cacho (já localizado pelo detector de cachos) e diz a variedade,
entre as cinco do Embrapa WGISD. Hiperparâmetros: imagem 224 px, até 60 épocas com parada após 15 sem
melhora na validação, aumentos de cor fracos (a cor da baga é informação da variedade), espelhamento
horizontal, rotação e escala moderadas; sem espelhamento vertical.

Saída: ml/runs/<nome>/weights/best.pt. Publicação: python ml/export_variety.py --run ml/runs/<nome>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUGMENT = {"hsv_h": 0.005, "hsv_s": 0.2, "hsv_v": 0.3, "fliplr": 0.5, "flipud": 0.0, "degrees": 10.0, "scale": 0.3, "erasing": 0.2}


def main() -> None:
    p = argparse.ArgumentParser(description="Treina o classificador YOLO de variedades")
    p.add_argument("--data", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-variedades")
    p.add_argument("--model", default="yolo26n-cls.pt", help="pesos iniciais (.pt) ou arquitetura (.yaml, do zero)")
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--imgsz", type=int, default=224)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--patience", type=int, default=15)
    p.add_argument("--device", default=None)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--name", default="oasis-variedades")
    args = p.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.model)
    model.train(
        data=str(args.data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, patience=args.patience,
        device=args.device, workers=args.workers, seed=args.seed, deterministic=True, cos_lr=True,
        project=str(ROOT / "ml" / "runs"), name=args.name, exist_ok=True, **AUGMENT,
    )
    trainer = model.trainer
    summary = {
        "model": args.model, "pretrained": str(args.model).endswith(".pt"), "data": str(args.data), "epochs_requested": args.epochs,
        "imgsz": args.imgsz, "batch": args.batch, "patience": args.patience, "seed": args.seed, "augment": AUGMENT,
        "best": str(trainer.best), "val_metrics": {k: round(float(v), 4) for k, v in (trainer.metrics or {}).items()},
    }
    (Path(trainer.save_dir) / "oasis_train_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Melhores pesos: {trainer.best}\nPróximo passo: python ml/export_variety.py --run {trainer.save_dir}")


if __name__ == "__main__":
    main()
