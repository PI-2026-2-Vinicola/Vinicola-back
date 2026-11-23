"""
Armazenamento das imagens. Toda imagem é reprocessada antes de gravar: orientação corrigida, metadados EXIF
(incluindo GPS) removidos, tamanho limitado e miniatura gerada para listagens.

Dois destinos, escolhidos por STORAGE_BACKEND:
- "local" (padrão): pasta STORAGE_DIR no disco do servidor;
- "s3": bucket compatível com S3 (Supabase Storage, Cloudflare R2, Backblaze B2, MinIO, AWS). Necessário em
  hospedagens com disco temporário, como o plano gratuito do Render, que apaga os arquivos a cada reinício.

As chaves gravadas no banco (`image_path`, `thumb_path`) são as mesmas nos dois casos: AAAA/MM/DD/<id>.jpg.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from PIL import Image, ImageOps

from ..config import get_settings
from .s3 import S3Client, S3Error

MAX_SIDE = 1600
THUMB_SIDE = 360

Image.MAX_IMAGE_PIXELS = 40_000_000  # protege contra "bombas" de descompressão


class InvalidImage(ValueError):
    pass


class StorageError(RuntimeError):
    """Falha ao gravar, ler ou apagar no destino das imagens (rede, credenciais, bucket)."""


def load_image(data: bytes) -> Image.Image:
    """
    Abre a imagem já no tamanho em que será analisada e guardada (lado maior de até MAX_SIDE px).

    Fotos de celular têm 12 MP ou mais; decodificá-las inteiras custa cerca de 36 MB por cópia e várias centenas
    de MB durante a análise. JPEGs são decodificados direto em escala reduzida (`draft`), o que economiza
    memória e CPU, e o resultado é igual ao da imagem que fica armazenada.
    """
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in {"JPEG", "PNG", "WEBP"}:
            raise InvalidImage("Formato não suportado. Envie JPEG, PNG ou WEBP.")
        width, height = img.size
        scale = MAX_SIDE / max(width, height)
        if img.format == "JPEG" and scale < 1:
            img.draft("RGB", (max(1, int(width * scale)), max(1, int(height * scale))))
        img.load()
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise InvalidImage("Arquivo não é uma imagem válida ou é grande demais.") from exc
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return img


def _encode(img: Image.Image, side: int, quality: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


class Storage(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str = "") -> Iterator[tuple[str, int]]: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        """Resolve a chave garantindo que o arquivo fique dentro da pasta de armazenamento."""
        root = self.root.resolve()
        path = (root / key).resolve()
        if root not in path.parents:
            raise FileNotFoundError(key)
        return path

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except FileNotFoundError:
            pass

    def list(self, prefix: str = "") -> Iterator[tuple[str, int]]:
        if not self.root.exists():
            return
        for p in sorted(self.root.rglob("*")):
            key = p.relative_to(self.root).as_posix()
            if p.is_file() and not p.name.startswith(".") and key.startswith(prefix):
                yield key, p.stat().st_size


class S3Storage:
    name = "s3"

    def __init__(self, client):
        self.client = client

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except (S3Error, httpx.HTTPError) as exc:  # FileNotFoundError (objeto inexistente) passa direto
            raise StorageError(f"Armazenamento de imagens indisponível: {exc}") from exc

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        self._call(self.client.put, key, data, content_type)

    def get(self, key: str) -> bytes:
        return self._call(self.client.get, key)

    def delete(self, key: str) -> None:
        self._call(self.client.delete, key)

    def list(self, prefix: str = "") -> Iterator[tuple[str, int]]:
        return iter(self._call(lambda: list(self.client.list(prefix))))


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(S3Client(settings.s3_endpoint_url, settings.s3_region, settings.s3_bucket,
                                  settings.s3_access_key_id, settings.s3_secret_access_key))
    return LocalStorage(settings.storage_dir)


def save_image(img: Image.Image, captured_at: datetime) -> tuple[str, str]:
    """Grava a imagem normalizada e a miniatura; retorna as chaves (caminhos relativos)."""
    storage = get_storage()
    name = f"{captured_at:%Y/%m/%d}/{uuid.uuid4().hex}"
    full, thumb = f"{name}.jpg", f"{name}_thumb.jpg"
    storage.put(full, _encode(img, MAX_SIDE, 88))
    try:
        storage.put(thumb, _encode(img, THUMB_SIDE, 80))
    except StorageError:
        storage.delete(full)
        raise
    return full, thumb


def read_image(key: str) -> bytes:
    """Bytes de uma imagem guardada; FileNotFoundError se não existir mais."""
    return get_storage().get(key)


def delete_images(*keys: str | None) -> None:
    """Apaga imagens sem interromper quem chamou se o destino estiver fora do ar (o arquivo fica órfão)."""
    storage = get_storage()
    for key in keys:
        if key:
            try:
                storage.delete(key)
            except StorageError:
                pass


def storage_size_mb() -> float | None:
    """Espaço ocupado pelas imagens; None se o destino não responder."""
    try:
        return round(sum(size for _, size in get_storage().list()) / 1_048_576, 2)
    except StorageError:
        return None
