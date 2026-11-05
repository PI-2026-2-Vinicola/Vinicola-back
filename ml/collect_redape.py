"""
COLETA via API do Redape — Repositório de Dados de Pesquisa da Embrapa (plataforma Dataverse).

    python ml/collect_redape.py buscar "videira"                    # datasets que citam o termo
    python ml/collect_redape.py arquivos doi:10.48432/XA1OVL --filtro Videira
    python ml/collect_redape.py baixar   doi:10.48432/XA1OVL --filtro Videira   # PDDB: doenças da videira

Endpoints usados (API pública do Dataverse, sem autenticação para dados publicados), conferidos em
05/11/2026 no servidor do Redape (Dataverse 5.12.1):
- GET /api/search?q=<termo>&type=dataset&per_page=&start=      busca paginada
- GET /api/datasets/:persistentId/?persistentId=<doi>          metadados, licença e lista de arquivos (com MD5)
- GET /api/access/datafile/<id>                                download de um arquivo

Cada arquivo baixado é conferido pelo MD5 publicado. Um manifesto (redape-manifesto.json) registra DOI,
versão, licença, autores, citação e os arquivos baixados. O endereço do servidor vem de REDAPE_URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REDAPE_URL = os.environ.get("REDAPE_URL", "https://www.redape.dados.embrapa.br").rstrip("/")
USER_AGENT = "OASIS/2.0 (Projeto Integrador; coleta de dados de pesquisa)"
DEFAULT_DEST = ROOT / "ml" / "datasets" / "fontes" / "redape"


def client() -> httpx.Client:
    return httpx.Client(base_url=REDAPE_URL, timeout=httpx.Timeout(60, read=600), headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def get_json(c: httpx.Client, path: str, params: dict | None = None, retries: int = 3) -> dict:
    """GET com nova tentativa em falha temporária (429/5xx), com espera crescente."""
    for attempt in range(retries):
        res = c.get(path, params=params)
        if res.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(2 ** (attempt + 1))
            continue
        res.raise_for_status()
        body = res.json()
        if body.get("status") != "OK":
            raise RuntimeError(f"Redape respondeu {body.get('status')}: {body.get('message')}")
        return body["data"]
    raise RuntimeError("Redape indisponível")


def search(c: httpx.Client, query: str, per_page: int = 50) -> list[dict]:
    items, start = [], 0
    while True:
        data = get_json(c, "/api/search", {"q": query, "type": "dataset", "per_page": per_page, "start": start})
        items += data["items"]
        start += per_page
        if start >= data["total_count"] or not data["items"]:
            return items


def field(version: dict, name: str):
    for f in version["metadataBlocks"]["citation"]["fields"]:
        if f["typeName"] == name:
            return f["value"]
    return None


def dataset(c: httpx.Client, doi: str) -> dict:
    data = get_json(c, "/api/datasets/:persistentId/", {"persistentId": doi})
    v = data["latestVersion"]
    lic = v.get("license")
    authors = [a["authorName"]["value"] for a in field(v, "author") or []]
    return {
        "doi": doi,
        "titulo": field(v, "title"),
        "versao": f"{v.get('versionNumber')}.{v.get('versionMinorNumber')}",
        "publicado_em": v.get("releaseTime"),
        "licenca": lic.get("name") if isinstance(lic, dict) else lic,
        "licenca_url": lic.get("uri") if isinstance(lic, dict) else None,
        "autores": authors,
        "arquivos": [
            {"id": f["dataFile"]["id"], "nome": f["dataFile"]["filename"], "bytes": f["dataFile"].get("filesize"),
             "md5": f["dataFile"].get("md5") or (f["dataFile"].get("checksum") or {}).get("value"), "tipo": f["dataFile"].get("contentType")}
            for f in v.get("files", [])
        ],
    }


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(c: httpx.Client, ds: dict, dest: Path, name_filter: str | None, extra: tuple[str, ...] = ()) -> list[dict]:
    dest.mkdir(parents=True, exist_ok=True)
    chosen = [f for f in ds["arquivos"] if (not name_filter or name_filter.lower() in f["nome"].lower()) or f["nome"] in extra]
    done = []
    for f in chosen:
        out = dest / f["nome"]
        if not (out.exists() and md5_file(out) == f["md5"]):
            tmp = out.with_suffix(out.suffix + ".part")
            with c.stream("GET", f"/api/access/datafile/{f['id']}") as res:
                res.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in res.iter_bytes(1 << 20):
                        fh.write(chunk)
            if md5_file(tmp) != f["md5"]:
                tmp.unlink()
                raise SystemExit(f"MD5 divergente em {f['nome']}: download corrompido, tente de novo.")
            tmp.replace(out)
        done.append({**f, "arquivo": out.name})
        print(f"  ok  {f['nome']}  ({(f['bytes'] or 0) / 1e6:.1f} MB, MD5 conferido)")
    return done


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Coleta de dados de pesquisa da Embrapa pelo Redape (Dataverse)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar")
    b.add_argument("termo")
    a = sub.add_parser("arquivos")
    a.add_argument("doi")
    a.add_argument("--filtro")
    d = sub.add_parser("baixar")
    d.add_argument("doi")
    d.add_argument("--filtro", help="só arquivos cujo nome contém o texto (ex.: Videira)")
    d.add_argument("--dest", type=Path)
    args = p.parse_args(argv)

    with client() as c:
        if args.cmd == "buscar":
            for it in search(c, args.termo):
                print(f"{it['global_id']}  {it['name']}")
            return
        ds = dataset(c, args.doi)
        print(f"{ds['titulo']} · versão {ds['versao']} · licença {ds['licenca']}")
        if args.cmd == "arquivos":
            for f in ds["arquivos"]:
                if not args.filtro or args.filtro.lower() in f["nome"].lower():
                    print(f"  {f['id']:>6}  {(f['bytes'] or 0) / 1e6:8.1f} MB  {f['nome']}")
            return
        dest = args.dest or DEFAULT_DEST / args.doi.split("/")[-1]
        docs = tuple(f["nome"] for f in ds["arquivos"] if f["nome"].lower().endswith((".pdf", ".txt")))
        files = download(c, ds, dest, args.filtro, extra=docs)
        manifest = {**{k: v for k, v in ds.items() if k != "arquivos"}, "servidor": REDAPE_URL, "filtro": args.filtro,
                    "coletado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"), "arquivos": files}
        (dest / "redape-manifesto.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(files)} arquivos em {dest}")


if __name__ == "__main__":
    main()
