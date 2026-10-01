"""
Armazenamento local das imagens. Toda imagem é reprocessada antes de gravar:
orientação corrigida, metadados EXIF (incluindo GPS) removidos, tamanho limitado
e miniatura gerada para listagens. Pode ser trocado por S3, GCS ou Firebase Storage.
"""

import io
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from ..config import get_settings

MAX_SIDE = 1600
THUMB_SIDE = 360

Image.MAX_IMAGE_PIXELS = 40_000_000  # protege contra "bombas" de descompressão


class InvalidImage(ValueError):
    pass


def load_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise InvalidImage("Arquivo não é uma imagem válida ou é grande demais.") from exc
    if img.format not in {"JPEG", "PNG", "WEBP"}:
        raise InvalidImage("Formato não suportado. Envie JPEG, PNG ou WEBP.")
    return ImageOps.exif_transpose(img).convert("RGB")


def _encode(img: Image.Image, side: int, quality: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((side, side))
    out = io.BytesIO()
    copy.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def save_image(img: Image.Image, captured_at: datetime) -> tuple[str, str]:
    """Grava a imagem normalizada e a miniatura; retorna os caminhos relativos."""
    root = Path(get_settings().storage_dir)
    folder = root / captured_at.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    full, thumb = folder / f"{name}.jpg", folder / f"{name}_thumb.jpg"
    full.write_bytes(_encode(img, MAX_SIDE, 88))
    thumb.write_bytes(_encode(img, THUMB_SIDE, 80))
    return str(full.relative_to(root)), str(thumb.relative_to(root))


def image_file(relative: str) -> Path:
    """Resolve um caminho relativo garantindo que ele fique dentro do diretório de armazenamento."""
    root = Path(get_settings().storage_dir).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise FileNotFoundError(relative)
    return path


def storage_size_mb() -> float:
    root = Path(get_settings().storage_dir)
    if not root.exists():
        return 0.0
    return round(sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) / 1_048_576, 2)
