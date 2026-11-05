"""
ETAPA 2 — TREINAMENTO do detector YOLO da OASIS.

    pip install -r requirements-ml.txt
    python ml/train.py                                       # transfer learning a partir de yolo26n.pt (recomendado)
    python ml/train.py --model yolo26n.yaml --epochs 150     # do zero, sem pesos pré-treinados

Dataset padrão: ml/datasets/wgisd-cachos/data.yaml (gerado por ml/prepare_dataset.py a partir das
fotos reais do Embrapa WGISD). Também aceita o dataset próprio da OASIS (ml/dataset.yaml).

Saída: ml/runs/<nome>/ — pesos (weights/best.pt e last.pt), curvas, results.csv e args.yaml.
Para publicar o modelo na API (ONNX + ficha com as métricas de teste): python ml/export_model.py.

Hiperparâmetros e aumentos de dados (somente durante o treino, sobre as fotos reais):
- imgsz 640, batch 16, otimizador automático do Ultralytics, cosine LR, paciência 30 épocas;
- --nbs controla a acumulação de gradiente: com o padrão 64 e batch 16, o otimizador só dá um passo a
  cada 4 lotes (≈ 3 passos por época com 205 imagens); no treino do zero foi usado --nbs 16;
- cor: hsv_h 0,015 · hsv_s 0,5 · hsv_v 0,4 (luz do campo varia ao longo do dia);
- geometria: rotação ±5°, translação 10 %, escala ±50 %, espelhamento horizontal 50 %;
  sem espelhamento vertical (cachos pendem para baixo) e sem mixup (sobrepõe cenas irreais);
- mosaico ligado, desligado nas 10 últimas épocas para o modelo terminar em imagens inteiras.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import GENERIC_CLUSTER_CLASS, MODEL_CLASSES  # noqa: E402

DEFAULT_DATA = ROOT / "ml" / "datasets" / "wgisd-cachos" / "data.yaml"
AUGMENT = {
    "hsv_h": 0.015, "hsv_s": 0.5, "hsv_v": 0.4, "degrees": 5.0, "translate": 0.1, "scale": 0.5,
    "fliplr": 0.5, "flipud": 0.0, "mosaic": 1.0, "mixup": 0.0, "close_mosaic": 10,
}


def dataset_classes(yaml_path: Path) -> list[str]:
    from ultralytics.utils import YAML  # leitor de YAML do próprio Ultralytics

    names = YAML.load(yaml_path)["names"]
    ordered = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    if ordered != [GENERIC_CLUSTER_CLASS] and ordered != MODEL_CLASSES:
        raise SystemExit(
            f"Classes do dataset incompatíveis com a API: {ordered}.\n"
            f"Use [{GENERIC_CLUSTER_CLASS!r}] (detecção de cachos) ou as classes de app/catalog.py → MODEL_CLASSES, nesta ordem."
        )
    return ordered


def main() -> None:
    p = argparse.ArgumentParser(description="Treina o detector YOLO da OASIS")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p.add_argument("--model", default="yolo26n.pt", help="pesos iniciais (.pt, transfer learning) ou arquitetura (.yaml, do zero)")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--nbs", type=int, default=64, help="lote nominal (acumulação de gradiente = nbs / batch); use o valor de --batch em datasets pequenos")
    p.add_argument("--optimizer", default="auto")
    p.add_argument("--lr0", type=float, default=None, help="taxa de aprendizado inicial (padrão: a do otimizador)")
    p.add_argument("--patience", type=int, default=30, help="épocas sem melhora na validação antes de parar")
    p.add_argument("--device", default=None, help="ex.: 0 (GPU) ou cpu")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--name", default="oasis-cachos")
    p.add_argument("--cache", default="disk", help="disk (determinístico), ram ou none")
    args = p.parse_args()

    classes = dataset_classes(args.data)
    from ultralytics import YOLO

    model = YOLO(args.model)
    extra = {"lr0": args.lr0} if args.lr0 is not None else {}
    model.train(
        data=str(args.data),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        nbs=args.nbs,
        optimizer=args.optimizer,
        patience=args.patience,
        device=args.device,
        workers=args.workers,
        seed=args.seed,
        deterministic=True,
        cos_lr=True,
        cache=False if args.cache == "none" else args.cache,
        project=str(ROOT / "ml" / "runs"),
        name=args.name,
        exist_ok=True,
        **AUGMENT,
        **extra,
    )
    trainer = model.trainer
    summary = {
        "model": args.model,
        "pretrained": str(args.model).endswith(".pt"),
        "data": str(args.data),
        "classes": classes,
        "epochs_requested": args.epochs,
        "epochs_run": int(getattr(trainer, "epoch", -1)) + 1,
        "best_epoch": int(getattr(trainer, "best_fitness_epoch", -1)) if hasattr(trainer, "best_fitness_epoch") else None,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "nbs": args.nbs,
        "optimizer": args.optimizer,
        "lr0": args.lr0,
        "patience": args.patience,
        "seed": args.seed,
        "augment": AUGMENT,
        "best": str(trainer.best),
        "val_metrics": {k: round(float(v), 4) for k, v in (trainer.metrics or {}).items()},
    }
    (Path(trainer.save_dir) / "oasis_train_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Melhores pesos: {trainer.best}")
    print(f"Próximo passo: python ml/export_model.py --run {trainer.save_dir}")


if __name__ == "__main__":
    main()
