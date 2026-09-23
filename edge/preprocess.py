"""
Processamento inicial no edge: descarta capturas inúteis e prepara a imagem para a nuvem.

- brilho médio (capturas noturnas ou escuras)
- nitidez pela variância do Laplaciano (imagens borradas ou com a lente suja)
- redimensionamento (o YOLO usa 640 px; mantemos até 1280 px para auditoria)
- recompressão JPEG e remoção de metadados EXIF
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps, ImageStat

MIN_BRIGHTNESS = 35.0
MAX_BRIGHTNESS = 245.0
MIN_SHARPNESS = 12.0
MAX_SIDE = 1280
JPEG_QUALITY = 85

LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


@dataclass
class EdgeResult:
    accepted: bool
    reason: str | None
    brightness: float
    sharpness: float
    width: int
    height: int
    image: bytes | None


def analyze_quality(img: Image.Image) -> tuple[float, float]:
    gray = img.convert("L")
    brightness = ImageStat.Stat(gray).mean[0]
    small = gray.copy()
    small.thumbnail((640, 640))
    sharpness = ImageStat.Stat(small.filter(LAPLACIAN)).var[0]
    return brightness, sharpness


def preprocess(data: bytes) -> EdgeResult:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return EdgeResult(False, "arquivo não é uma imagem válida", 0, 0, 0, 0, None)

    brightness, sharpness = analyze_quality(img)
    reason = None
    if brightness < MIN_BRIGHTNESS:
        reason = "imagem muito escura"
    elif brightness > MAX_BRIGHTNESS:
        reason = "imagem superexposta"
    elif sharpness < MIN_SHARPNESS:
        reason = "imagem sem nitidez (borrada ou lente suja)"
    if reason:
        return EdgeResult(False, reason, round(brightness, 1), round(sharpness, 1), img.width, img.height, None)

    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)  # sem EXIF
    return EdgeResult(True, None, round(brightness, 1), round(sharpness, 1), img.width, img.height, out.getvalue())
