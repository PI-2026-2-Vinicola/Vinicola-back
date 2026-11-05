"""
ETAPA 1 → 2 — PREPARO: transforma as imagens reais do WGISD no dataset YOLO de treinamento.

    python ml/prepare_dataset.py                       # dataset de cachos (1 classe)
    python ml/prepare_dataset.py --task variedades     # experimento: uma classe por variedade do WGISD

Passos (todos registrados em preparo.json):
1. validação — a imagem precisa abrir e decodificar por completo; cada linha de rótulo é conferida
   (5 campos, classe 0, coordenadas normalizadas); caixas que passam da borda são recortadas e
   caixas degeneradas, descartadas. Imagem sem nenhuma caixa válida é excluída;
2. duplicatas — arquivos idênticos (SHA-256) são excluídos; quase-duplicatas (dHash) são apenas
   agrupadas, para nunca ficarem em divisões diferentes;
3. qualidade — brilho, contraste e nitidez de cada imagem são medidos e anotados nos metadados. As
   fotos NÃO são descartadas por isso: variação de foco e luz faz parte do campo (e do datasheet);
4. normalização — RGB 8 bits, JPEG, sem EXIF, lado maior limitado (padrão 1024 px, proporção mantida,
   então as caixas normalizadas continuam exatas);
5. divisão — o teste é a divisão oficial do WGISD (58 imagens, comparável com a literatura); a
   validação sai do treino oficial, estratificada por variedade e com semente fixa.

Nenhuma imagem é gerada artificialmente: o aumento de dados (cor, escala, espelhamento, mosaico)
acontece só durante o treinamento, sobre as fotos reais, e nada disso é gravado no dataset.
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

from PIL import Image  # noqa: E402

from ml.dataset_tools import (  # noqa: E402
    dhash,
    format_yolo_labels,
    image_quality,
    near_duplicate_groups,
    parse_yolo_labels,
    quality_flags,
    resize_max_side,
    stratified_holdout,
    write_data_yaml,
)
from ml.sources import WGISD_COMMIT, WGISD_LICENSE, WGISD_REPO, WGISD_VARIETIES  # noqa: E402

SPLITS = ("train", "val", "test")
CLUSTER_CLASS = "cacho"
VARIETY_CLASSES = {p: v["name"].lower().replace(" ", "_") for p, v in WGISD_VARIETIES.items()}


def class_names(task: str) -> list[str]:
    return [CLUSTER_CLASS] if task == "cachos" else [VARIETY_CLASSES[p] for p in sorted(VARIETY_CLASSES)]


def load_source_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise SystemExit(f"Metadados da coleta não encontrados ({path}). Rode antes: python ml/collect_wgisd.py")
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def main() -> None:
    p = argparse.ArgumentParser(description="Preparo do dataset YOLO a partir do Embrapa WGISD")
    p.add_argument("--source", type=Path, default=ROOT / "ml" / "datasets" / "fontes" / "wgisd")
    p.add_argument("--metadata", type=Path, default=ROOT / "ml" / "datasets" / "fontes" / "wgisd-metadados.csv")
    p.add_argument("--out", type=Path, help="padrão: ml/datasets/wgisd-<task>")
    p.add_argument("--task", choices=["cachos", "variedades"], default="cachos")
    p.add_argument("--max-side", type=int, default=1024)
    p.add_argument("--val-fraction", type=float, default=0.15)
    p.add_argument("--near-dup-bits", type=int, default=6, help="distância máxima de dHash (de 64 bits) para quase-duplicata")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--jpeg-quality", type=int, default=92)
    p.add_argument("--report-dir", type=Path, help="cópia versionada do relatório e dos metadados (padrão: ml/reports/wgisd-<task>)")
    args = p.parse_args()

    out: Path = args.out or ROOT / "ml" / "datasets" / f"wgisd-{args.task}"
    meta = load_source_metadata(args.metadata)
    names = class_names(args.task)
    data_dir = args.source / "data"

    excluded: list[dict] = []
    label_issues: dict[str, list[str]] = {}
    clipped = dropped = 0
    records: dict[str, dict] = {}
    seen_sha: dict[str, str] = {}
    hashes: dict[str, int] = {}

    # 1–3. validação, duplicatas exatas e qualidade
    for image_id in sorted(meta):
        row = meta[image_id]
        img_path = data_dir / f"{image_id}.jpg"
        try:
            with Image.open(img_path) as im:
                im.load()  # decodifica por completo: arquivo truncado ou corrompido falha aqui
                img = im.convert("RGB")
                mode = im.mode
        except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura exclui a imagem
            excluded.append({"id": image_id, "motivo": f"imagem inválida: {exc}"})
            continue
        if row["sha256"] in seen_sha:
            excluded.append({"id": image_id, "motivo": f"duplicata exata de {seen_sha[row['sha256']]}"})
            continue
        seen_sha[row["sha256"]] = image_id

        label_path = img_path.with_suffix(".txt")
        check = parse_yolo_labels(label_path.read_text() if label_path.exists() else "", num_classes=1)
        clipped += check.clipped
        dropped += check.dropped
        if check.issues:
            label_issues[image_id] = check.issues
        if not check.boxes:
            excluded.append({"id": image_id, "motivo": "nenhuma caixa válida"})
            continue
        cls = 0 if args.task == "cachos" else names.index(VARIETY_CLASSES[row["prefixo"]])
        boxes = [(cls, cx, cy, w, h) for _, cx, cy, w, h in check.boxes]

        quality = image_quality(img)
        hashes[image_id] = dhash(img)
        records[image_id] = {"row": row, "img": img, "mode": mode, "boxes": boxes, "quality": quality, "flags": quality_flags(quality)}

    # 2. quase-duplicatas (mantidas, mas sempre juntas na mesma divisão)
    groups = near_duplicate_groups(hashes, args.near_dup_bits)
    cross_split = [g for g in groups if len({records[i]["row"]["divisao_oficial"] for i in g}) > 1]

    # 5. divisão
    official_train = [i for i, r in records.items() if r["row"]["divisao_oficial"] == "treino"]
    val = stratified_holdout(official_train, key=lambda i: records[i]["row"]["prefixo"], fraction=args.val_fraction, seed=args.seed, groups=groups)
    for i, r in records.items():
        r["split"] = "test" if r["row"]["divisao_oficial"] == "teste" else "val" if i in val else "train"

    # 4. gravação no formato YOLO
    if out.exists():
        shutil.rmtree(out)
    for s in SPLITS:
        (out / "images" / s).mkdir(parents=True)
        (out / "labels" / s).mkdir(parents=True)
    group_of = {i: "+".join(g) for g in groups for i in g}
    rows_out = []
    for i in sorted(records):
        r = records[i]
        img = resize_max_side(r["img"], args.max_side)
        img.save(out / "images" / r["split"] / f"{i}.jpg", "JPEG", quality=args.jpeg_quality, optimize=True)  # sem EXIF
        (out / "labels" / r["split"] / f"{i}.txt").write_text(format_yolo_labels(r["boxes"]))
        rows_out.append({
            **{k: v for k, v in r["row"].items() if k not in ("largura", "altura")},
            "divisao": r["split"],
            "largura_fonte": r["row"]["largura"],
            "altura_fonte": r["row"]["altura"],
            "largura": img.width,
            "altura": img.height,
            "cachos_validos": len(r["boxes"]),
            "classe": CLUSTER_CLASS if args.task == "cachos" else VARIETY_CLASSES[r["row"]["prefixo"]],
            "brilho": r["quality"]["brightness"],
            "contraste": r["quality"]["contrast"],
            "nitidez": r["quality"]["sharpness"],
            "alertas_qualidade": ";".join(r["flags"]),
            "dhash": f"{hashes[i]:016x}",
            "grupo_quase_duplicata": group_of.get(i, ""),
        })

    with open(out / "metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        w.writeheader()
        w.writerows(rows_out)

    write_data_yaml(
        out / "data.yaml", out, names,
        header=[
            f"Dataset OASIS ({args.task}) gerado por ml/prepare_dataset.py em {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
            f"Fonte: Embrapa WGISD — {WGISD_REPO} @ {WGISD_COMMIT[:10]} — licença {WGISD_LICENSE} (uso não comercial, com atribuição).",
        ],
    )

    # relatório do preparo
    stats: dict[str, dict] = {s: {"imagens": 0, "cachos": 0, "por_variedade": Counter()} for s in SPLITS}
    box_sizes = defaultdict(list)
    for r in rows_out:
        s = stats[r["divisao"]]
        s["imagens"] += 1
        s["cachos"] += r["cachos_validos"]
        s["por_variedade"][r["variedade"]] += 1
    for i, r in records.items():
        for _, _, _, w, h in r["boxes"]:
            box_sizes[r["split"]].append((w * h) ** 0.5)
    report = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tarefa": args.task,
        "classes": names,
        "fonte": {"nome": "Embrapa WGISD", "repositorio": WGISD_REPO, "commit": WGISD_COMMIT, "licenca": WGISD_LICENSE, "imagens_na_fonte": len(meta),
                  "cachos_na_fonte": sum(int(r["cachos_anotados"]) for r in meta.values())},
        "parametros": {"lado_maximo_px": args.max_side, "fracao_validacao": args.val_fraction, "semente": args.seed,
                       "quase_duplicata_bits": args.near_dup_bits, "qualidade_jpeg": args.jpeg_quality},
        "divisoes": {s: {"imagens": v["imagens"], "cachos": v["cachos"], "por_variedade": dict(sorted(v["por_variedade"].items()))} for s, v in stats.items()},
        "tamanho_relativo_dos_cachos": {s: {"mediana": round(sorted(v)[len(v) // 2], 4), "min": round(min(v), 4), "max": round(max(v), 4)} for s, v in box_sizes.items() if v},
        "excluidas": excluded,
        "rotulos": {"caixas_recortadas_na_borda": clipped, "linhas_descartadas": dropped, "problemas": label_issues},
        "quase_duplicatas": {"grupos": groups, "grupos_entre_treino_e_teste_oficiais": cross_split},
        "alertas_de_qualidade": dict(Counter(f for r in records.values() for f in r["flags"])),
        "modos_de_cor_na_fonte": dict(Counter(r["mode"] for r in records.values())),
    }
    (out / "preparo.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_dir = args.report_dir or ROOT / "ml" / "reports" / f"wgisd-{args.task}"
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in ("preparo.json", "metadata.csv"):
        shutil.copy(out / name, report_dir / name)  # o dataset fica fora do git; o relatório e os metadados, não

    print(f"Dataset: {out}")
    for s in SPLITS:
        print(f"  {s:5}: {stats[s]['imagens']:3} imagens · {stats[s]['cachos']:4} cachos · {dict(sorted(stats[s]['por_variedade'].items()))}")
    print(f"Excluídas: {len(excluded)} · caixas recortadas: {clipped} · linhas descartadas: {dropped} · grupos de quase-duplicatas: {len(groups)}")
    print(f"Alertas de qualidade (não excluem): {report['alertas_de_qualidade']}")


if __name__ == "__main__":
    main()
