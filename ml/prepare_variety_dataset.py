"""
Dataset de VARIEDADES: recortes de cada cacho anotado do Embrapa WGISD, uma pasta por variedade.

    python ml/prepare_variety_dataset.py          # a partir de ml/datasets/wgisd-cachos (prepare_dataset.py)

Cada caixa anotada vira um recorte quadrado (a caixa ampliada até ficar quadrada, com 10 % de margem),
para o classificador ver o cacho inteiro sem distorção. As divisões são as MESMAS do detector, por foto:
recortes da mesma foto nunca ficam em divisões diferentes. Cachos muito pequenos (lado < 48 px na
imagem de 1024 px) são ignorados — são os cachos distantes, no fundo.

Saída (formato de classificação do Ultralytics):
    ml/datasets/wgisd-variedades/{train,val,test}/<variedade>/<foto>_<n>.jpg
    ml/datasets/wgisd-variedades/recortes.csv   (recorte → foto, variedade, câmera, data, divisão)
    ml/reports/wgisd-variedades/recortes.json   (contagens, versionado)
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.services.detector import square_crop_box  # noqa: E402  (o mesmo recorte usado pela API)
from ml.prepare_dataset import VARIETY_CLASSES  # noqa: E402

SPLITS = ("train", "val", "test")


def main(argv: list[str] | None = None) -> dict:
    p = argparse.ArgumentParser(description="Recortes dos cachos do WGISD por variedade (classificação)")
    p.add_argument("--source", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-cachos")
    p.add_argument("--out", type=Path, default=ROOT / "ml" / "datasets" / "wgisd-variedades")
    p.add_argument("--min-side", type=int, default=48)
    p.add_argument("--report-dir", type=Path, default=ROOT / "ml" / "reports" / "wgisd-variedades")
    args = p.parse_args(argv)

    with open(args.source / "metadata.csv", encoding="utf-8") as f:
        meta = {r["id"]: r for r in csv.DictReader(f)}
    if args.out.exists():
        shutil.rmtree(args.out)
    rows, counts, skipped = [], defaultdict(Counter), 0
    for split in SPLITS:
        for img_path in sorted((args.source / "images" / split).glob("*.jpg")):
            m = meta[img_path.stem]
            cls = VARIETY_CLASSES[m["prefixo"]]
            img = Image.open(img_path).convert("RGB")
            W, H = img.size
            lines = (args.source / "labels" / split / f"{img_path.stem}.txt").read_text().split("\n")
            for k, line in enumerate(x for x in lines if x.strip()):
                _, cx, cy, w, h = (float(v) for v in line.split())
                if max(w * W, h * H) < args.min_side:
                    skipped += 1
                    continue
                box = square_crop_box(cx, cy, w, h, W, H)
                out = args.out / split / cls / f"{img_path.stem}_{k:02d}.jpg"
                out.parent.mkdir(parents=True, exist_ok=True)
                img.crop(box).save(out, "JPEG", quality=92)
                counts[split][cls] += 1
                rows.append({"recorte": out.relative_to(args.out).as_posix(), "foto": img_path.stem, "variedade": cls, "divisao": split,
                             "camera": m["camera"], "data_captura": m["data_captura"], "lado_px": round(max(w * W, h * H))})
    with open(args.out / "recortes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    report = {
        "fonte": str(args.source.relative_to(ROOT)) if args.source.is_relative_to(ROOT) else str(args.source),
        "classes": sorted(VARIETY_CLASSES.values()),
        "lado_minimo_px": args.min_side,
        "recortes_por_divisao": {s: dict(sorted(c.items())) for s, c in counts.items()},
        "totais": {s: sum(c.values()) for s, c in counts.items()},
        "cachos_ignorados_por_tamanho": skipped,
        "observacao": "Divisões por foto, iguais às do detector; recortes da mesma foto ficam sempre na mesma divisão.",
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "recortes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    main()
