"""
ETAPA 2 → 3 — EXPORTAÇÃO E AVALIAÇÃO: publica o modelo treinado para a API.

    python ml/export_model.py --run ml/runs/oasis-cachos-yolo26n

1. exporta os melhores pesos (best.pt) para ONNX (entrada fixa 640 × 640) — a API roda o modelo
   com o ONNX Runtime, sem PyTorch;
2. avalia o ONNX com o MESMO código de inferência da API (app/services/detector.py): na validação,
   onde se escolhe o limiar de confiança de maior F1, e no teste oficial do WGISD, que não foi usado
   no treino nem na escolha do limiar;
3. roda a validação do Ultralytics no teste (matriz de confusão e curvas P, R, F1 e PR);
4. confere a paridade entre o ONNX e os pesos PyTorch nas imagens de teste;
5. grava models/<nome>.onnx + models/<nome>.json (ficha do modelo) e ml/reports/<nome>/.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.detector import OnnxYoloDetector  # noqa: E402
from app.services.model_card import ModelCard, card_path, file_sha256  # noqa: E402
from ml.evaluation import metrics_for, per_variety, run_split  # noqa: E402
from ml.metrics import iou_xywh  # noqa: E402
from ml.sources import WGISD_INSTITUTION, WGISD_LICENSE, WGISD_LICENSE_URL, WGISD_REPO, WGISD_SITE  # noqa: E402

PLOTS = ["confusion_matrix.png", "confusion_matrix_normalized.png", "BoxPR_curve.png", "BoxF1_curve.png", "BoxP_curve.png", "BoxR_curve.png"]


def read_args_yaml(run: Path) -> dict:
    from ultralytics.utils import YAML

    return YAML.load(run / "args.yaml")


def best_epoch(run: Path) -> dict:
    """Linha de results.csv com a melhor aptidão (0,1·mAP50 + 0,9·mAP50-95, critério do Ultralytics)."""
    with open(run / "results.csv", encoding="utf-8") as f:
        rows = [{k.strip(): v for k, v in r.items()} for r in csv.DictReader(f)]
    def fitness(r):
        return 0.1 * float(r["metrics/mAP50(B)"]) + 0.9 * float(r["metrics/mAP50-95(B)"])
    best = max(rows, key=fitness)
    return {"epoch": int(float(best["epoch"])), "epochs_run": len(rows), "val_map50": round(float(best["metrics/mAP50(B)"]), 4), "val_map50_95": round(float(best["metrics/mAP50-95(B)"]), 4)}


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def architecture_name(model: str) -> str:
    """yolo26n.yaml / yolo26n.pt → YOLO26n."""
    stem = Path(model).stem
    return f"YOLO{stem[4:]}" if stem.lower().startswith("yolo") else stem


def split_metrics(name: str, items, confidence: float) -> dict:
    m = metrics_for(items, confidence)
    return {"split": name, "images": len(items), "instances": m["instances"], "precision": m["precision"], "recall": m["recall"],
            "f1": m["f1"], "map50": m["map50"], "map50_95": m["map50_95"], "confidence": m["confidence"]}


def parity(pt_model, onnx_detector, dataset: Path, confidence: float, imgsz: int) -> dict:
    """Compara as caixas do ONNX (pipeline da API) com as do PyTorch (Ultralytics) nas imagens de teste."""
    from PIL import Image

    matched, total_pt, total_onnx, ious = 0, 0, 0, []
    for img_path in sorted((dataset / "images" / "test").glob("*.jpg")):
        img = Image.open(img_path).convert("RGB")
        res = pt_model.predict(img, conf=confidence, imgsz=imgsz, verbose=False)[0]
        pt_boxes = [(cx - w / 2, cy - h / 2, w, h) for cx, cy, w, h in res.boxes.xywhn.tolist()]
        onnx_boxes = [d.box for d in onnx_detector.predict(img) if d.confidence >= confidence]
        total_pt += len(pt_boxes)
        total_onnx += len(onnx_boxes)
        for b in pt_boxes:
            best = max((iou_xywh(b, o) for o in onnx_boxes), default=0.0)
            if best >= 0.5:
                matched += 1
                ious.append(best)
    return {
        "caixas_pytorch": total_pt,
        "caixas_onnx": total_onnx,
        "caixas_pytorch_com_par_no_onnx_iou50": round(matched / max(1, total_pt), 4),
        "iou_medio_dos_pares": round(statistics.fmean(ious), 4) if ious else None,
        "observacao": "O Ultralytics usa letterbox retangular nos pesos .pt; o ONNX usa entrada quadrada fixa — pequenas diferenças são esperadas.",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Exporta o modelo treinado para ONNX, avalia e publica a ficha do modelo")
    p.add_argument("--run", type=Path, required=True, help="pasta do treino (ml/runs/<nome>)")
    p.add_argument("--name", default="oasis-cachos", help="nome dos arquivos em models/")
    p.add_argument("--model-name", default="OASIS Cachos")
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--out-dir", type=Path, default=ROOT / "models")
    p.add_argument("--calibration", type=Path, default=ROOT / "ml" / "reports" / "calibracao_cor.json")
    p.add_argument("--opset", type=int, default=17)
    args = p.parse_args()

    from ultralytics import YOLO, __version__ as ul_version
    import onnxruntime
    import torch

    run = args.run.resolve()
    train_args = read_args_yaml(run)
    summary_path = run / "oasis_train_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    data_yaml = Path(train_args["data"])
    dataset = data_yaml.parent
    prep = json.loads((dataset / "preparo.json").read_text(encoding="utf-8"))
    imgsz = int(train_args.get("imgsz", 640))
    best_pt = run / "weights" / "best.pt"
    reports = ROOT / "ml" / "reports" / args.name
    reports.mkdir(parents=True, exist_ok=True)

    # 1. exportação
    pt_model = YOLO(str(best_pt))
    # nms=False: cabeça sem NMS (ponta a ponta) quando a arquitetura tem uma, como o YOLO26 — a mesma
    # usada pela validação do Ultralytics; nas demais, saída bruta e NMS feito pela API.
    exported = Path(pt_model.export(format="onnx", imgsz=imgsz, opset=args.opset, simplify=True, dynamic=False, nms=False))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out_dir / f"{args.name}.onnx"
    shutil.copy(exported, onnx_path)
    out_shape = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"]).get_outputs()[0].shape
    end2end = len(out_shape) == 3 and out_shape[-1] == 6 and out_shape[1] != 4 + len(pt_model.names)

    calibration = json.loads(args.calibration.read_text(encoding="utf-8")) if args.calibration.exists() else None
    color = None
    if calibration:
        t = calibration["limiares_calibrados"]
        color = {"brown_mild": t["brown_mild"], "brown_severe": t["brown_severe"], "mold": t["mold"], "off_color": t["off_color"],
                 "target_rate": calibration["meta_de_disparo"], "report": "ml/reports/calibracao_cor.json"}

    classes = [pt_model.names[i] for i in sorted(pt_model.names)]
    split_counts = prep["divisoes"]
    card = {
        "name": args.model_name,
        "version": args.version,
        "task": "deteccao_cachos" if classes == ["cacho"] else "deteccao_completa",
        "architecture": architecture_name(str(summary.get("model") or train_args.get("model", "yolo"))),
        "framework": f"Ultralytics {ul_version} · PyTorch {torch.__version__.split('+')[0]} → ONNX (opset {args.opset}) · ONNX Runtime {onnxruntime.__version__}",
        "input_size": imgsz,
        "classes": classes,
        "confidence_threshold": 0.25,  # provisório; substituído pelo limiar escolhido na validação
        "iou_threshold": float(train_args.get("iou", 0.7)),
        "trained_at": datetime.fromtimestamp(best_pt.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "training": {},
        "metrics": {},
        "dataset": {
            "name": "Embrapa WGISD",
            "source_url": WGISD_REPO,
            "version": prep["fonte"]["commit"],
            "license": WGISD_LICENSE,
            "license_url": WGISD_LICENSE_URL,
            "institution": WGISD_INSTITUTION,
            "description": "Fotografias de campo de cachos de uva para vinho com caixas anotadas manualmente (Wine Grape Instance Segmentation Dataset).",
            "images": {**{s: v["imagens"] for s, v in split_counts.items()}, "total": sum(v["imagens"] for v in split_counts.values())},
            "instances": {**{s: v["cachos"] for s, v in split_counts.items()}, "total": sum(v["cachos"] for v in split_counts.values())},
            "varieties": sorted({v for s in split_counts.values() for v in s["por_variedade"]}),
            "capture": f"{WGISD_SITE['local']}, {WGISD_SITE['municipio']}-{WGISD_SITE['uf']} · 27/04/2017 (Syrah) e 27/04/2018 · Canon EOS REBEL T3i e Motorola Moto Z2 Play",
        },
        "sha256": file_sha256(onnx_path),
        "license": "Uso não comercial, com atribuição: pesos derivados do Embrapa WGISD (CC BY-NC 4.0).",
        "limitations": [],
        "pretrained": bool(summary.get("pretrained", str(train_args.get("model", "")).endswith(".pt"))),
        "end2end": end2end,
        "color_thresholds": color,
    }
    card_file = card_path(onnx_path)
    card_file.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. avaliação com o pipeline da API
    detector = OnnxYoloDetector(onnx_path, confidence=0.001)
    val_items, _ = run_split(detector, dataset, "val")
    test_items, times = run_split(detector, dataset, "test")
    chosen = metrics_for(val_items, None)["confidence"]
    threshold = max(0.05, round(chosen - 0.005, 2))  # limiar de maior F1 na validação, arredondado para baixo
    metrics = {"val": split_metrics("validação", val_items, threshold), "test": split_metrics("teste", test_items, threshold)}
    test_detail = metrics_for(test_items, threshold)

    # 3. validação do Ultralytics no teste (gráficos)
    ul = pt_model.val(data=str(data_yaml), split="test", imgsz=imgsz, batch=16, conf=0.001, iou=card["iou_threshold"], plots=True,
                      project=str(reports / "_ultralytics"), name="teste", exist_ok=True, verbose=False)
    ul_p, ul_r = float(ul.box.mp), float(ul.box.mr)
    ul_metrics = {"precision": round(ul_p, 4), "recall": round(ul_r, 4), "f1": round(2 * ul_p * ul_r / max(ul_p + ul_r, 1e-9), 4),
                  "map50": round(float(ul.box.map50), 4), "map50_95": round(float(ul.box.map), 4)}
    for name in PLOTS:
        src = Path(ul.save_dir) / name
        if src.exists():
            shutil.copy(src, reports / name)
    shutil.rmtree(reports / "_ultralytics", ignore_errors=True)

    # 4. paridade ONNX × PyTorch
    parity_report = parity(pt_model, OnnxYoloDetector(onnx_path, confidence=0.001), dataset, threshold, imgsz)

    epoch = best_epoch(run)
    card["confidence_threshold"] = threshold
    card["metrics"] = metrics
    card["inference_ms_cpu"] = round(statistics.median(times), 1)
    card["training"] = {
        "pesos_iniciais": summary.get("model", train_args.get("model")),
        "epocas_solicitadas": int(train_args.get("epochs", 0)),
        "epocas_executadas": epoch["epochs_run"],
        "melhor_epoca": epoch["epoch"],
        "paciencia": int(train_args.get("patience", 0)),
        "imgsz": imgsz,
        "batch": int(train_args.get("batch", 0)),
        "otimizador": train_args.get("optimizer"),
        "lr0": train_args.get("lr0"),
        "cos_lr": train_args.get("cos_lr"),
        "semente": train_args.get("seed"),
        "aumentos": {k: train_args.get(k) for k in ("hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "fliplr", "flipud", "mosaic", "mixup", "close_mosaic")},
        "dispositivo": str(train_args.get("device")),
        "metricas_ultralytics_teste": ul_metrics,
    }
    card["limitations"] = [
        f"Treinado somente com as {card['dataset']['images']['total']} fotos do Embrapa WGISD: uma única propriedade ({WGISD_SITE['local']}, {WGISD_SITE['municipio']}-{WGISD_SITE['uf']}), duas datas e duas câmeras.",
        "Localiza cachos de uva. Não identifica a variedade nem diagnostica doenças, qualidade ou maturação — esses itens vêm do cadastro do talhão e da análise de cor, que é uma regra, não um modelo treinado.",
        "Ainda não foi avaliado com fotos do Vale do São Francisco nem das câmeras ESP32-CAM da OASIS; o desempenho pode cair até que imagens locais sejam anotadas e incluídas no treino.",
        "Treinado do zero (sem pesos pré-treinados)." if not card["pretrained"] else "Treinado por transfer learning a partir de pesos pré-treinados do Ultralytics.",
        f"Detecções com confiança abaixo de {threshold:.2f} são descartadas (limiar de maior F1 na validação).".replace(".", ",", 1),
        "Uso não comercial: os pesos derivam do WGISD (CC BY-NC 4.0); citar a Embrapa e os autores do dataset.",
    ]
    ModelCard.model_validate(card)  # a ficha publicada precisa ser válida para a API
    card_file.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "modelo": f"{args.model_name} {args.version}",
        "arquivos": {"onnx": rel(onnx_path), "ficha": rel(card_file), "sha256": card["sha256"], "formato_saida": list(out_shape), "ponta_a_ponta": end2end},
        "limiar": {"escolhido_na_validacao": threshold, "criterio": "maior F1 (IoU 0,5) na validação"},
        "pipeline_api": {"validacao": metrics["val"], "teste": metrics["test"], "teste_detalhe": test_detail,
                         "teste_por_variedade": per_variety(test_items, dataset, threshold),
                         "tempo_inferencia_ms_cpu": {"mediana": round(statistics.median(times), 1), "p95": round(sorted(times)[int(0.95 * (len(times) - 1))], 1)}},
        "ultralytics_teste": ul_metrics,
        "paridade_onnx_pytorch": parity_report,
        "treino": card["training"] | {"melhor_epoca_validacao": epoch},
    }
    (reports / "avaliacao.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("modelo", "limiar", "pipeline_api", "ultralytics_teste", "paridade_onnx_pytorch")}, ensure_ascii=False, indent=1))
    print(f"Publicado: {onnx_path} + {card_file}")


if __name__ == "__main__":
    main()
