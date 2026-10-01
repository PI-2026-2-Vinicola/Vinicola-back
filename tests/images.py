"""Imagens sintéticas para exercitar a análise de cor (cacho sobre folhagem)."""

import io
import random

from PIL import Image, ImageDraw

TINTA = (62, 26, 72)
BRANCA = (192, 200, 92)
BROWN = (122, 76, 30)
LEAF = (70, 120, 50)


def grape_cluster(berry=TINTA, rot: float = 0.0, size=(800, 600), seed: int = 1, cluster: bool = True) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", size, LEAF)
    draw = ImageDraw.Draw(img)
    for _ in range(300):  # textura de folhas
        x, y, r = rng.randint(0, size[0]), rng.randint(0, size[1]), rng.randint(6, 20)
        g = rng.randint(95, 140)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(rng.randint(50, 80), g, rng.randint(35, 60)))
    if cluster:
        cx, top, rows = size[0] // 2, int(size[1] * 0.18), 9
        for row in range(rows):
            width = max(1, 8 - row * 7 // rows)
            y = top + row * 38
            for i in range(width):
                x = cx + (i - (width - 1) / 2) * 40 + rng.randint(-4, 4)
                color = BROWN if rot and rng.random() < rot else tuple(max(0, min(255, c + rng.randint(-8, 8))) for c in berry)
                draw.ellipse((x - 21, y - 21, x + 21, y + 21), fill=color)
    return img


def jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
