"""
DATASET PRÓPRIO — importa as anotações revisadas (Label Studio ou CVAT, formato YOLO) e monta o
dataset de treino com as classes da OASIS.

    python ml/import_annotations.py --annotations caminho/da/exportacao --metadata ml/datasets/oasis-campo/metadata.csv

Fluxo completo:
    python -m app.cli export-dataset          → fotos reais + pré-anotações (ml/datasets/oasis-campo)
    revisão no Label Studio ou no CVAT        → exportar em formato YOLO
    python ml/import_annotations.py           → ml/datasets/oasis-campo-anotado (train/val/test + data.yaml)
    python ml/train.py --data ml/datasets/oasis-campo-anotado/data.yaml
    python ml/export_model.py --run ml/runs/<nome>

As classes são remapeadas pelo NOME (classes.txt, obj.names ou data.yaml da ferramenta) para a ordem de
app/catalog.py → MODEL_CLASSES, porque cada ferramenta numera as classes do seu jeito. As divisões são
feitas por sensor e dia de captura: fotos quase iguais do mesmo dispositivo nunca ficam em divisões
diferentes (o que inflaria as métricas).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import MODEL_CLASSES  # noqa: E402
from ml.dataset_tools import format_yolo_labels, parse_yolo_labels, write_data_yaml  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
NAME_FILES = ("classes.txt", "obj.names")


def tool_class_names(root: Path) -> list[str]:
    for name in NAME_FILES:
        found = sorted(root.rglob(name))
        if found:
            return [line.strip() for line in found[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    yamls = sorted(root.rglob("data.yaml"))
    if yamls:
        names: dict[int, str] = {}
        in_names = False
        for line in yamls[0].read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("names:"):
                in_names = True
                continue
            if in_names and ":" in line and line.startswith((" ", "\t")):
                k, v = line.split(":", 1)
                if k.strip().isdigit():
                    names[int(k)] = v.strip().strip("'\"")
            elif in_names and line.strip():
                break
        if names:
            return [names[i] for i in sorted(names)]
    raise SystemExit(f"Nenhum arquivo de classes (classes.txt, obj.names ou data.yaml) em {root}.")


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description="Importa anotações revisadas (YOLO) para o dataset da OASIS")
    p.add_argument("--annotations", type=Path, required=True, help="pasta exportada pelo Label Studio/CVAT (formato YOLO)")
    p.add_argument("--metadata", type=Path, default=ROOT / "ml" / "datasets" / "oasis-campo" / "metadata.csv")
    p.add_argument("--out", type=Path, default=ROOT / "ml" / "datasets" / "oasis-campo-anotado")
    p.add_argument("--val", type=float, default=0.2)
    p.add_argument("--test", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    names = tool_class_names(args.annotations)
    unknown = [n for n in names if n not in MODEL_CLASSES]
    if unknown:
        raise SystemExit(f"Classes fora do catálogo da OASIS: {unknown}. Use os nomes de app/catalog.py → MODEL_CLASSES.")
    remap = {i: MODEL_CLASSES.index(n) for i, n in enumerate(names)}
    images = {p.stem: p for p in args.annotations.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES}
    labels = {p.stem: p for p in args.annotations.rglob("*.txt") if p.name not in NAME_FILES and p.name != "train.txt"}
    meta = {}
    if args.metadata.exists():
        with open(args.metadata, encoding="utf-8") as f:
            meta = {r["leitura"]: r for r in csv.DictReader(f)}

    samples, issues, without_image = {}, {}, []
    for stem, label in sorted(labels.items()):
        if stem not in images:
            without_image.append(stem)
            continue
        check = parse_yolo_labels(label.read_text(encoding="utf-8"), num_classes=len(names))
        if check.issues:
            issues[stem] = check.issues
        samples[stem] = [(remap[c], cx, cy, w, h) for c, cx, cy, w, h in check.boxes]

    # divisão por grupo sensor + dia (sem metadados, cada imagem é um grupo)
    groups: dict[str, list[str]] = defaultdict(list)
    for stem in samples:
        m = meta.get(stem)
        groups[f"{m['sensor']}|{m['capturada_em'][:10]}" if m else stem].append(stem)
    keys = sorted(groups)
    random.Random(args.seed).shuffle(keys)
    total = len(samples)
    split_of: dict[str, str] = {}
    counts = Counter()
    for k in keys:
        split = "test" if counts["test"] < args.test * total else "val" if counts["val"] < args.val * total else "train"
        for stem in groups[k]:
            split_of[stem] = split
            counts[split] += 1

    if args.out.exists():
        shutil.rmtree(args.out)
    for s in ("train", "val", "test"):
        (args.out / "images" / s).mkdir(parents=True)
        (args.out / "labels" / s).mkdir(parents=True)
    per_class = Counter()
    for stem, boxes in samples.items():
        s = split_of[stem]
        shutil.copy(images[stem], args.out / "images" / s / f"{stem}{images[stem].suffix.lower()}")
        (args.out / "labels" / s / f"{stem}.txt").write_text(format_yolo_labels(boxes), encoding="utf-8")
        per_class.update(MODEL_CLASSES[b[0]] for b in boxes)
    write_data_yaml(args.out / "data.yaml", args.out, MODEL_CLASSES, header=["Dataset próprio da OASIS (anotações revisadas) — ml/import_annotations.py"])
    report = {
        "imagens": total, "divisoes": dict(counts), "grupos_sensor_dia": len(groups), "caixas_por_classe": dict(per_class),
        "classes_sem_exemplos": [c for c in MODEL_CLASSES if per_class[c] == 0], "rotulos_sem_imagem": without_image, "problemas": issues,
    }
    (args.out / "importacao.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    main()
