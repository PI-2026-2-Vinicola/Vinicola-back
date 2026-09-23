import io
import random

from PIL import Image, ImageDraw

from edge.preprocess import preprocess


def image_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def textured(size=(1600, 1200)) -> Image.Image:
    rng = random.Random(1)
    img = Image.new("RGB", size, (70, 110, 50))
    draw = ImageDraw.Draw(img)
    for _ in range(400):
        x, y, r = rng.randint(0, size[0]), rng.randint(0, size[1]), rng.randint(8, 30)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(rng.randint(30, 90), rng.randint(20, 60), rng.randint(60, 120)))
    return img


def test_accepts_and_resizes_good_image():
    res = preprocess(image_bytes(textured()))
    assert res.accepted
    assert max(res.width, res.height) <= 1280
    assert res.image and Image.open(io.BytesIO(res.image)).format == "JPEG"


def test_rejects_dark_image():
    res = preprocess(image_bytes(Image.new("RGB", (800, 600), (5, 5, 5))))
    assert not res.accepted and "escura" in res.reason


def test_rejects_blurry_image():
    res = preprocess(image_bytes(Image.new("RGB", (800, 600), (120, 130, 110))))
    assert not res.accepted and "nitidez" in res.reason


def test_rejects_invalid_file():
    assert not preprocess(b"abc").accepted
