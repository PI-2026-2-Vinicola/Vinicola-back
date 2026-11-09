"""
ETAPA 2 → 3 — EXPORTAÇÃO E AVALIAÇÃO do classificador de variedades.

    python ml/export_variety.py --run ml/runs/oasis-variedades

1. exporta best.pt para ONNX (entrada 224 × 224);
2. avalia os recortes de VALIDAÇÃO e de TESTE com o código de inferência da API: acurácia, acurácia
   balanceada, precisão/recall/F1 por variedade e matriz de confusão;
3. avalia a variedade POR FOTO no teste com o fluxo completo da API (detector de cachos publicado →
   recortes → classificador → votação ponderada) — a medida que o usuário vê;
4. verifica atalhos: acurácia separada por câmera (Canon × Motorola) — se o modelo tivesse aprendido a
   câmera ou o dia da foto em vez da variedade, o desempenho cairia muito numa das câmeras;
5. grava models/oasis-variedades.onnx + .json e ml/reports/oasis-variedades/.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.catalog import MODEL_VARIETY_NAMES, variety_from_class  # noqa: E402
from app.services.detector import BUNDLED_MODEL, OnnxVarietyClassifier, OnnxYoloDetector  # noqa: E402
from app.services.model_card import ModelCard, card_path, file_sha256  # noqa: E402
from ml.sources import WGISD_INSTITUTION, WGISD_LICENSE, WGISD_LICENSE_URL, WGISD_REPO, WGISD_SITE  # noqa: E402


def classification_report(y_true: list[str], y_pred: list[str], classes: list[str]) -> tuple[dict, list[list[int]]]:
    idx = {c: i for i, c in enumerate(classes)}
    cm = np.zeros((len(classes), len(classes)), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[idx[t], idx[p]] += 1
    per_class = {}
    for c, i in idx.items():
        tp = cm[i, i]
        prec = tp / cm[:, i].sum() if cm[:, i].sum() else 0.0
        rec = tp / cm[i, :].sum() if cm[i, :].sum() else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[c] = {"precision": round(float(prec), 4), "recall": round(float(rec), 4), "f1": round(float(f1), 4), "support": int(cm[i, :].sum())}
    acc = float(np.trace(cm) / cm.sum()) if cm.sum() else 0.0
    bal = float(np.mean([v["recall"] for v in per_class.values() if v["support"]]))
    return {"accuracy": round(acc, 4), "balanced_accuracy": round(bal, 4), "per_class": per_class}, cm.tolist()


def evaluate_crops(clf: OnnxVarietyClassifier, dataset: Path, split: str, crops_meta: dict[str, dict]) -> dict:
    y_true, y_pred, by_camera, photos = [], [], defaultdict(lambda: [0, 0]), set()
    for path in sorted((dataset / split).rglob("*.jpg")):
        true = path.parent.name
        pred = clf.classes[int(clf.probabilities(Image.open(path).convert("RGB")).argmax())]
        y_true.append(true)
        y_pred.append(pred)
        m = crops_meta.get(path.relative_to(dataset).as_posix(), {})
        photos.add(m.get("foto", path.stem.rsplit("_", 1)[0]))
        cam = m.get("camera", "?")
        by_camera[cam][0] += pred == true
        by_camera[cam][1] += 1
    report, cm = classification_report(y_true, y_pred, clf.classes)
    report.update({"split": split, "samples": len(y_true), "images": len(photos), "confusion_matrix": cm,
                   "accuracy_by_camera": {c: {"accuracy": round(ok / n, 4), "samples": n} for c, (ok, n) in sorted(by_camera.items())}})
    return report


def evaluate_photos(detector: OnnxYoloDetector, wgisd: Path, meta: dict[str, dict]) -> dict:
    """Fluxo completo da API em cada foto de teste: cachos detectados → variedade por votação."""
    from ml.prepare_dataset import VARIETY_CLASSES

    results, per_variety = [], defaultdict(lambda: [0, 0])
    for img_path in sorted((wgisd / "images" / "test").glob("*.jpg")):
        true = VARIETY_CLASSES[meta[img_path.stem]["prefixo"]]
        out = detector.detect(Image.open(img_path).convert("RGB"), None, meta[img_path.stem]["tipo"])
        pred = out.variety.label if out.variety else None
        results.append({"foto": img_path.stem, "verdadeira": true, "prevista": pred, "confianca": out.variety.confidence if out.variety else None,
                        "cachos_votantes": out.variety.clusters if out.variety else 0})
        per_variety[true][0] += pred == true
        per_variety[true][1] += 1
    ok = sum(r["prevista"] == r["verdadeira"] for r in results)
    return {"photos": len(results), "accuracy": round(ok / len(results), 4),
            "by_variety": {v: {"accuracy": round(a / n, 4), "photos": n} for v, (a, n) in sorted(per_variety.items())},
            "without_prediction": sum(r["prevista"] is None for r in results), "details": results}


def plot_confusion(cm: list[list[int]], classes: list[str], out: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arr = np.array(cm)
    fig, ax = plt.subplots(figsize=(6.4, 5.4))
    ax.imshow(arr, cmap="Purples")
    labels = [MODEL_VARIETY_NAMES.get(c, c) for c in classes]
    ax.set_xticks(range(len(classes)), labels, rotation=35, ha="right")
    ax.set_yticks(range(len(classes)), labels)
    ax.set_xlabel("Prevista")
    ax.set_ylabel("Verdadeira")
    ax.set_title(title)
    for i in range(len(classes)):
        for j in range(len(classes)):
            ax.text(j, i, int(arr[i, j]), ha="center", va="center", color="white" if arr[i, j] > arr.max() / 2 else "black", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(description="Exporta e avalia o classificador de variedades")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--name", default="oasis-variedades")
    p.add_argument("--model-name", default="OASIS Variedades")
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--out-dir", type=Path, default=ROOT / "models")
    p.add_argument("--crops", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-variedades")
    p.add_argument("--wgisd", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-cachos")
    p.add_argument("--detector", type=Path, default=BUNDLED_MODEL)
    p.add_argument("--opset", type=int, default=17)
    args = p.parse_args()

    import onnxruntime
    import torch
    from ultralytics import YOLO, __version__ as ul_version
    from ultralytics.utils import YAML

    run = args.run.resolve()
    train_args = YAML.load(run / "args.yaml")
    summary = json.loads((run / "oasis_train_summary.json").read_text(encoding="utf-8")) if (run / "oasis_train_summary.json").exists() else {}
    imgsz = int(train_args.get("imgsz", 224))
    reports = ROOT / "ml" / "reports" / args.name
    reports.mkdir(parents=True, exist_ok=True)

    pt = YOLO(str(run / "weights" / "best.pt"))
    exported = Path(pt.export(format="onnx", imgsz=imgsz, opset=args.opset, simplify=True, dynamic=False))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out_dir / f"{args.name}.onnx"
    shutil.copy(exported, onnx_path)
    classes = [pt.names[i] for i in sorted(pt.names)]

    with open(args.crops / "recortes.csv", encoding="utf-8") as f:
        crops_meta = {r["recorte"]: r for r in csv.DictReader(f)}
    with open(args.wgisd / "metadata.csv", encoding="utf-8") as f:
        meta = {r["id"]: r for r in csv.DictReader(f)}
    counts = Counter((r["divisao"], r["foto"]) for r in crops_meta.values())
    photos_per_split = Counter(s for s, _ in counts)
    samples_per_split = Counter(r["divisao"] for r in crops_meta.values())

    with open(run / "results.csv", encoding="utf-8") as f:
        rows = [{k.strip(): v for k, v in r.items()} for r in csv.DictReader(f)]
    best = max(rows, key=lambda r: float(r["metrics/accuracy_top1"]))

    card = {
        "name": args.model_name, "version": args.version, "task": "classificacao_variedade",
        "architecture": "YOLO26n-cls", "pretrained": bool(summary.get("pretrained", True)),
        "framework": f"Ultralytics {ul_version} · PyTorch {torch.__version__.split('+')[0]} → ONNX (opset {args.opset}) · ONNX Runtime {onnxruntime.__version__}",
        "input_size": imgsz, "classes": classes, "confidence_threshold": 0.5, "iou_threshold": 0.0,
        "trained_at": datetime.fromtimestamp((run / "weights" / "best.pt").stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "training": {}, "metrics": {},
        "dataset": {
            "name": "Embrapa WGISD (recortes dos cachos)", "source_url": WGISD_REPO, "version": "6910edc5ae3aae8c20062941b1641821f0c30127",
            "license": WGISD_LICENSE, "license_url": WGISD_LICENSE_URL, "institution": WGISD_INSTITUTION,
            "description": "Recortes quadrados de cada cacho anotado, uma pasta por variedade; divisões por foto iguais às do detector.",
            "images": {**dict(photos_per_split), "total": sum(photos_per_split.values())},
            "instances": {**dict(samples_per_split), "total": sum(samples_per_split.values())},
            "varieties": [MODEL_VARIETY_NAMES.get(c, c) for c in classes],
            "capture": f"{WGISD_SITE['local']}, {WGISD_SITE['municipio']}-{WGISD_SITE['uf']} · Syrah em 27/04/2017; demais em 27/04/2018",
        },
        "sha256": file_sha256(onnx_path), "license": f"Uso não comercial, com atribuição: pesos derivados do Embrapa WGISD ({WGISD_LICENSE}).",
        "limitations": [],
    }
    card_file = card_path(onnx_path)
    card_file.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    clf = OnnxVarietyClassifier(onnx_path)
    val = evaluate_crops(clf, args.crops, "val", crops_meta)
    test = evaluate_crops(clf, args.crops, "test", crops_meta)
    detector = OnnxYoloDetector(args.detector, variety=clf)
    photos = evaluate_photos(detector, args.wgisd, meta)
    plot_confusion(test["confusion_matrix"], classes, reports / "confusion_matrix_teste.png", "Variedade por recorte — teste")
    ul = pt.val(data=str(args.crops), split="test", imgsz=imgsz, batch=64, plots=False, verbose=False)

    for split, r in (("val", val), ("test", test)):
        card["metrics"][split] = {"split": "validação" if split == "val" else "teste", "images": r["images"], "samples": r["samples"],
                                  "accuracy": r["accuracy"], "balanced_accuracy": r["balanced_accuracy"],
                                  "per_class": r["per_class"], "photo_accuracy": photos["accuracy"] if split == "test" else None}
    card["training"] = {
        "pesos_iniciais": f"{summary.get('model', train_args.get('model'))} (ImageNet, release oficial da Ultralytics)",
        "epocas_solicitadas": int(train_args.get("epochs", 0)), "epocas_executadas": len(rows), "melhor_epoca": int(float(best["epoch"])),
        "imgsz": imgsz, "batch": int(train_args.get("batch", 0)), "otimizador": train_args.get("optimizer"), "paciencia": int(train_args.get("patience", 0)),
        "aumentos": {k: train_args.get(k) for k in ("hsv_h", "hsv_s", "hsv_v", "fliplr", "flipud", "degrees", "scale", "erasing")},
        "metricas_ultralytics_teste": {"top1": round(float(ul.top1), 4), "top5": round(float(ul.top5), 4)},
    }
    out_of_catalog = [MODEL_VARIETY_NAMES[c] for c in classes if variety_from_class(c) is None]
    card["limitations"] = [
        f"Conhece só {len(classes)} variedades ({', '.join(MODEL_VARIETY_NAMES.get(c, c) for c in classes)}) e sempre escolhe uma delas: para "
        "Tempranillo, Touriga Nacional, Chenin Blanc ou Moscato Canelli a previsão será, por construção, uma variedade errada.",
        f"{', '.join(out_of_catalog)} não estão no catálogo da OASIS; a variedade oficial da leitura continua sendo a do talhão.",
        "Treinado com fotos de um único vinhedo: cada variedade foi fotografada num único dia (Syrah em 2017, as demais em 2018), "
        "então o modelo pode ter aprendido parte das condições daquele dia. Precisa ser validado com fotos do Vale do São Francisco.",
        "Nas fotos de treino, os cachos tintos estavam verdes (antes do pintor): cachos maduros, roxos, são uma situação que o modelo não viu.",
        "Probabilidades abaixo de 50 % são exibidas como baixa confiança.",
        f"Uso não comercial: os pesos derivam do WGISD ({WGISD_LICENSE}).",
    ]
    ModelCard.model_validate(card)
    card_file.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"modelo": f"{args.model_name} {args.version}", "classes": classes, "validacao": val, "teste": test, "por_foto_teste": photos,
              "ultralytics_teste": card["training"]["metricas_ultralytics_teste"], "melhor_epoca": card["training"]["melhor_epoca"]}
    (reports / "avaliacao.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    brief = {k: v for k, v in report.items() if k != "por_foto_teste"} | {"por_foto_teste": {k: v for k, v in photos.items() if k != "details"}}
    print(json.dumps(brief, ensure_ascii=False, indent=1))
    print(f"Publicado: {onnx_path} + {card_file}")


if __name__ == "__main__":
    main()
