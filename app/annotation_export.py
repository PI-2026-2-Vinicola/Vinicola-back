"""
Exportação das imagens da própria OASIS para anotação (dataset próprio).

    python -m app.cli export-dataset --out ml/datasets/oasis-campo [--since 2026-10-01] [--sensor S-001]

Gera, a partir das leituras com imagem gravadas pela API (sensores e envios), um pacote pronto para
revisão humana no Label Studio ou no CVAT:

    <out>/images/train/<código>.jpg     fotos reais de campo (já sem EXIF)
    <out>/labels/train/<código>.txt     PRÉ-anotações no formato YOLO (classes de app/catalog.py → MODEL_CLASSES)
    <out>/data.yaml                     dataset no formato Ultralytics (importação "Ultralytics YOLO" no CVAT)
    <out>/label_studio/tasks.json       tarefas do Label Studio com as pré-anotações como "predictions"
    <out>/label_studio/config.xml       configuração de rótulos do Label Studio
    <out>/metadata.csv                  leitura, sensor, talhão, variedade, data, origem e resultado da análise

As pré-anotações vêm das detecções gravadas (modelo de cachos + regra de cor) e PRECISAM ser revisadas:
o objetivo é acelerar a anotação, não substituir o julgamento de um responsável técnico.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .catalog import ANOMALY_TEXT, GENERIC_CLUSTER_CLASS, MODEL_CLASSES, VARIETIES, yolo_class
from .models import Reading
from .services.storage import image_file

CLASS_ID = {c: i for i, c in enumerate(MODEL_CLASSES)}
VARIETY_NAME = {v["id"]: v["name"] for v in VARIETIES}
COLORS = ["#7a1f3d", "#3b5b2a", "#5b2a6e", "#1f4e79", "#b08d2b", "#c97c5d", "#e0a526", "#8a8a3b", "#d4762c", "#8b1e1e", "#6b4f3a", "#3a3a3a"]


def label_studio_config() -> str:
    labels = []
    for i, c in enumerate(MODEL_CLASSES):
        title = VARIETY_NAME.get(c.replace("_", "-")) or ANOMALY_TEXT[c][0]
        kind = "cacho" if c.replace("_", "-") in VARIETY_NAME else "anomalia"
        labels.append(f'    <Label value="{c}" hint="{title} ({kind})" background="{COLORS[i % len(COLORS)]}"/>')
    return "\n".join([
        "<View>",
        '  <Header value="OASIS · revise as caixas: um cacho por caixa (classe = variedade do talhão) e regiões com anomalia"/>',
        '  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="false"/>',
        '  <RectangleLabels name="label" toName="image" strokeWidth="2">',
        *labels,
        "  </RectangleLabels>",
        '  <Choices name="avaliacao" toName="image" choice="single" showInline="true">',
        '    <Choice value="boa" hint="Boa"/>',
        '    <Choice value="atencao" hint="Atenção"/>',
        '    <Choice value="critica" hint="Necessita atenção"/>',
        "  </Choices>",
        '  <TextArea name="observacao" toName="image" placeholder="Observação do responsável técnico (opcional)"/>',
        "</View>",
        "",
    ])


def _class_for(label: str, variety_id: str) -> int | None:
    if label == GENERIC_CLUSTER_CLASS:
        label = yolo_class(variety_id)
    return CLASS_ID.get(label)


def export_dataset(db: Session, out: Path, *, since: datetime | None = None, sensor_id: str | None = None,
                   limit: int | None = None, image_prefix: str = "/data/local-files/?d=") -> dict:
    query = db.query(Reading).filter(Reading.image_path.isnot(None), Reading.source.in_(("sensor", "upload")))
    if since:
        query = query.filter(Reading.captured_at >= since)
    if sensor_id:
        query = query.filter(Reading.sensor_id == sensor_id)
    readings = query.order_by(Reading.captured_at).limit(limit).all() if limit else query.order_by(Reading.captured_at).all()

    if out.exists():
        shutil.rmtree(out)
    (out / "images" / "train").mkdir(parents=True)
    (out / "labels" / "train").mkdir(parents=True)
    (out / "label_studio").mkdir()
    tasks, rows, missing, boxes_total = [], [], 0, 0
    for r in readings:
        try:
            src = image_file(r.image_path)
        except FileNotFoundError:
            missing += 1
            continue
        if not src.exists():
            missing += 1
            continue
        code = r.code
        dst = out / "images" / "train" / f"{code}.jpg"
        shutil.copy(src, dst)
        lines, results = [], []
        for d in r.detections:
            cls = _class_for(d.label, r.variety_id)
            if cls is None:
                continue
            lines.append(f"{cls} {d.x + d.w / 2:.6f} {d.y + d.h / 2:.6f} {d.w:.6f} {d.h:.6f}")
            results.append({
                "from_name": "label", "to_name": "image", "type": "rectanglelabels",
                "value": {"x": round(d.x * 100, 4), "y": round(d.y * 100, 4), "width": round(d.w * 100, 4), "height": round(d.h * 100, 4),
                          "rotation": 0, "rectanglelabels": [MODEL_CLASSES[cls]]},
                "score": round(d.confidence, 3),
            })
        boxes_total += len(lines)
        (out / "labels" / "train" / f"{code}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        relative = f"{out.name}/images/train/{code}.jpg"
        tasks.append({
            "data": {"image": f"{image_prefix}{relative}", "leitura": code, "sensor": r.sensor_id, "variedade": r.variety_id},
            "predictions": [{"model_version": r.model_version, "result": results}],
        })
        rows.append({
            "leitura": code, "sensor": r.sensor_id, "talhao": r.sensor.block if r.sensor else "", "variedade": r.variety_id,
            "capturada_em": r.captured_at.isoformat(), "origem": r.source, "modelo": r.model_version, "qualidade_prevista": r.quality,
            "classificacao_prevista": r.classification, "maturacao_prevista": r.maturation, "condicao_visual_prevista": r.visual_condition,
            "cachos_previstos": r.clusters_detected, "pre_anotacoes": len(lines), "arquivo": f"images/train/{code}.jpg",
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
        })

    names = "\n".join(f"  {i}: {c}" for i, c in enumerate(MODEL_CLASSES))
    (out / "data.yaml").write_text(
        "# Exportado pela OASIS para anotação — PRÉ-anotações a revisar (python -m app.cli export-dataset).\n"
        f"path: {out.resolve().as_posix()}\ntrain: images/train\nval: images/train\n\nnc: {len(MODEL_CLASSES)}\nnames:\n{names}\n",
        encoding="utf-8",
    )
    (out / "classes.txt").write_text("\n".join(MODEL_CLASSES) + "\n", encoding="utf-8")
    (out / "label_studio" / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "label_studio" / "config.xml").write_text(label_studio_config(), encoding="utf-8")
    with open(out / "metadata.csv", "w", newline="", encoding="utf-8") as f:
        fields = list(rows[0]) if rows else ["leitura"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return {"images": len(rows), "pre_annotations": boxes_total, "missing_files": missing, "out": str(out)}
