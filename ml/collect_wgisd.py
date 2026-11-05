"""
ETAPA 1 — COLETA: obtém as imagens reais do Embrapa WGISD e extrai os metadados de cada imagem.

    python ml/collect_wgisd.py                       # clona o repositório oficial na versão fixada
    python ml/collect_wgisd.py --from-path ../wgisd  # usa um clone já existente (conferido pela versão)

Saída: ml/datasets/fontes/wgisd/ (arquivos originais, intocados) e
       ml/datasets/fontes/wgisd-metadados.csv (uma linha por imagem).

Os metadados vêm só da fonte: variedade (prefixo do arquivo, conforme o datasheet), divisão oficial
treino/teste, número de cachos anotados, existência de máscara, câmera e data de captura (EXIF das
imagens em resolução original), dimensões, hash SHA-256, URL permanente e licença.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from ml.dataset_tools import sha256_file  # noqa: E402
from ml.sources import (  # noqa: E402
    WGISD_COMMIT,
    WGISD_EXPECTED,
    WGISD_INSTITUTION,
    WGISD_LICENSE,
    WGISD_REPO,
    WGISD_SITE,
    WGISD_VARIETIES,
    wgisd_file_url,
)

DEFAULT_DEST = ROOT / "ml" / "datasets" / "fontes" / "wgisd"
METADATA_FIELDS = [
    "id", "prefixo", "variedade", "tipo", "variedade_oasis", "divisao_oficial", "cachos_anotados", "possui_mascara",
    "largura", "altura", "largura_original", "altura_original", "camera", "data_captura", "sha256",
    "url_imagem", "url_anotacao", "licenca", "instituicao", "local", "municipio", "uf", "latitude", "longitude",
]


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch(dest: Path, commit: str) -> None:
    """Baixa somente a versão fixada (fetch raso por commit); se o servidor recusar, faz o clone completo."""
    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        git("init", "-q", cwd=dest)
        git("remote", "add", "origin", WGISD_REPO, cwd=dest)
    try:
        print(f"Baixando {WGISD_REPO} @ {commit[:10]} (≈2,7 GB)…", flush=True)
        subprocess.run(["git", "fetch", "--depth", "1", "origin", commit], cwd=dest, check=True)
        git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "fetch", "origin"], cwd=dest, check=True)
        git("checkout", "-q", "--detach", commit, cwd=dest)


def verify(dest: Path, commit: str) -> None:
    head = git("rev-parse", "HEAD", cwd=dest)
    if head != commit:
        raise SystemExit(f"{dest} está na versão {head[:10]}, não na versão fixada {commit[:10]}. Use --commit para outra versão.")
    origin = git("remote", "get-url", "origin", cwd=dest).rstrip("/").removesuffix(".git").lower()
    if origin != WGISD_REPO.lower():
        raise SystemExit(f"{dest} não é um clone de {WGISD_REPO} (origin = {origin}).")
    images = sorted((dest / "data").glob("*.jpg"))
    train = read_list(dest / "train.txt")
    test = read_list(dest / "test.txt")
    found = {"images": len(images), "train_images": len(train), "test_images": len(test)}
    if found != WGISD_EXPECTED:
        raise SystemExit(f"Conteúdo diferente do publicado no datasheet: {found} (esperado {WGISD_EXPECTED}).")


def read_list(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def exif_info(path: Path) -> tuple[str, str, tuple[int, int]]:
    """Câmera e data de captura (DateTimeOriginal) do EXIF da imagem em resolução original."""
    with Image.open(path) as im:
        exif = im.getexif()
        sub = exif.get_ifd(0x8769)
        make, model = str(exif.get(271, "")).strip(), str(exif.get(272, "")).strip()
        camera = model if model.lower().startswith(make.lower()) else f"{make} {model}".strip()
        raw_date = str(sub.get(36867) or exif.get(306) or "")
        date = raw_date[:10].replace(":", "-") if len(raw_date) >= 10 else ""
        return camera, date, im.size


def collect_metadata(dest: Path) -> list[dict]:
    train, test = set(read_list(dest / "train.txt")), set(read_list(dest / "test.txt"))
    rows = []
    for img_path in sorted((dest / "data").glob("*.jpg")):
        image_id = img_path.stem
        prefix = image_id.split("_")[0]
        variety = WGISD_VARIETIES.get(prefix)
        if variety is None:
            raise SystemExit(f"Prefixo desconhecido no WGISD: {image_id}")
        label = img_path.with_suffix(".txt")
        boxes = sum(1 for line in label.read_text().splitlines() if line.strip()) if label.exists() else 0
        original = dest / "original_resolution" / img_path.name
        camera, date, original_size = exif_info(original) if original.exists() else ("", "", (0, 0))
        with Image.open(img_path) as im:
            size = im.size
        rows.append({
            "id": image_id,
            "prefixo": prefix,
            "variedade": variety["name"],
            "tipo": variety["type"],
            "variedade_oasis": variety["oasis_id"] or "",
            "divisao_oficial": "teste" if image_id in test else "treino" if image_id in train else "",
            "cachos_anotados": boxes,
            "possui_mascara": img_path.with_suffix(".npz").exists(),
            "largura": size[0],
            "altura": size[1],
            "largura_original": original_size[0],
            "altura_original": original_size[1],
            "camera": camera,
            "data_captura": date,
            "sha256": sha256_file(img_path),
            "url_imagem": wgisd_file_url(f"data/{img_path.name}"),
            "url_anotacao": wgisd_file_url(f"data/{label.name}"),
            "licenca": WGISD_LICENSE,
            "instituicao": WGISD_INSTITUTION,
            "local": WGISD_SITE["local"],
            "municipio": WGISD_SITE["municipio"],
            "uf": WGISD_SITE["uf"],
            "latitude": WGISD_SITE["latitude"],
            "longitude": WGISD_SITE["longitude"],
        })
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="Coleta do Embrapa WGISD (imagens reais de cachos de uva)")
    p.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="pasta do clone")
    p.add_argument("--from-path", type=Path, help="usa um clone já existente em vez de baixar")
    p.add_argument("--commit", default=WGISD_COMMIT, help="versão do repositório (padrão: a usada no treinamento)")
    p.add_argument("--metadata", type=Path, default=DEFAULT_DEST.parent / "wgisd-metadados.csv", help="CSV de saída")
    args = p.parse_args()

    dest = args.from_path or args.dest
    if not args.from_path and not (dest / "data").exists():
        fetch(dest, args.commit)
    verify(dest, args.commit)
    rows = collect_metadata(dest)
    out = args.metadata
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=METADATA_FIELDS)
        w.writeheader()
        w.writerows(rows)
    by_variety: dict[str, int] = {}
    for r in rows:
        by_variety[r["variedade"]] = by_variety.get(r["variedade"], 0) + 1
    print(f"Fonte verificada: {dest} @ {args.commit[:10]}")
    print(f"{len(rows)} imagens, {sum(r['cachos_anotados'] for r in rows)} cachos anotados · {by_variety}")
    print(f"Metadados: {out}")


if __name__ == "__main__":
    main()
