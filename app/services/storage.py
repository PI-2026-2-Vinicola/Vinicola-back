"""Armazenamento local das imagens recebidas. Pode ser trocado por S3, GCS ou Firebase Storage."""

import uuid
from datetime import datetime
from pathlib import Path

from ..config import get_settings


def save_image(data: bytes, captured_at: datetime, suffix: str = ".jpg") -> str:
    root = Path(get_settings().storage_dir)
    folder = root / captured_at.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return str(path.relative_to(root))


def image_file(relative: str) -> Path:
    return Path(get_settings().storage_dir) / relative
