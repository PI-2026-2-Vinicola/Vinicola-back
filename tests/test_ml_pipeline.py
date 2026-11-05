"""Ferramentas do pipeline de dados (ml/): validação de rótulos, duplicatas, divisão e métricas de detecção."""

import pytest
from PIL import Image, ImageDraw

from ml.dataset_tools import clip_box, dhash, format_yolo_labels, near_duplicate_groups, parse_yolo_labels, quality_flags, stratified_holdout
from ml.metrics import evaluate, iou_xywh


def test_yolo_labels_are_validated_clipped_and_bad_lines_reported():
    text = "0 0.5 0.5 0.2 0.2\n0 0.95 0.5 0.2 0.2\n0 0.76 0.65 0.002 0.0009\n1 0.5 0.5 0.1 0.1\n0 0.5 abc 0.1 0.1\n0 0.5\n\n"
    check = parse_yolo_labels(text, num_classes=1)
    assert len(check.boxes) == 2 and check.clipped == 1 and check.dropped == 4
    _, cx, cy, w, h = check.boxes[1]
    assert cx + w / 2 == pytest.approx(1.0) and w == pytest.approx(0.15)  # recortada na borda direita
    assert any("degenerada" in i for i in check.issues) and any("classe 1" in i for i in check.issues)
    assert format_yolo_labels(check.boxes).splitlines()[0] == "0 0.500000 0.500000 0.200000 0.200000"
    assert clip_box(0.5, 0.5, -0.1, 0.2) is None


def _photo(seed: int) -> Image.Image:
    img = Image.new("RGB", (320, 240), (40, 90, 40))
    d = ImageDraw.Draw(img)
    for i in range(12):
        x = (seed * 37 + i * 53) % 300
        y = (seed * 17 + i * 29) % 220
        d.ellipse((x, y, x + 25 + i, y + 25 + i), fill=(60 + i * 10, 20, 70))
    return img


def test_near_duplicates_are_grouped_and_kept_in_the_same_split():
    a = _photo(1)
    near = a.copy()
    ImageDraw.Draw(near).rectangle((0, 0, 3, 3), fill=(255, 255, 255))  # quase igual (rajada)
    hashes = {"A_1": dhash(a), "A_2": dhash(near), "B_1": dhash(_photo(5)), "C_1": dhash(_photo(9))}
    groups = near_duplicate_groups(hashes, threshold=6)
    assert groups == [["A_1", "A_2"]]
    ids = [f"{p}_{i}" for p in "ABCD" for i in range(10)] + ["A_1", "A_2"]
    ids = sorted(set(ids))
    val = stratified_holdout(ids, key=lambda i: i[0], fraction=0.2, seed=42, groups=groups)
    assert val == stratified_holdout(ids, key=lambda i: i[0], fraction=0.2, seed=42, groups=groups)  # determinístico
    assert ("A_1" in val) == ("A_2" in val)  # o par nunca é separado
    for prefix in "BCD":
        assert sum(i.startswith(prefix) for i in val) == 2  # estratificado por variedade


def test_quality_flags():
    assert quality_flags({"brightness": 20, "contrast": 10, "sharpness": 3}) == ["escura", "baixo_contraste", "desfocada"]
    assert quality_flags({"brightness": 126, "contrast": 49, "sharpness": 1500}) == []


def test_detection_metrics_perfect_partial_and_threshold():
    gts = [(0.1, 0.1, 0.2, 0.2), (0.6, 0.6, 0.2, 0.2)]
    perfect = evaluate([([(0.9, gts[0]), (0.8, gts[1])], gts)])
    # 0,995: teto da interpolação de 101 pontos — o mesmo valor máximo reportado pelo Ultralytics
    assert perfect["map50"] == pytest.approx(0.995) and perfect["precision"] == 1 and perfect["recall"] == 1
    # uma predição certa, uma errada (sem sobreposição) e um cacho perdido
    preds = [(0.9, gts[0]), (0.7, (0.35, 0.35, 0.1, 0.1))]
    m = evaluate([(preds, gts)], confidence=0.5)
    assert m["true_positives"] == 1 and m["false_positives"] == 1 and m["false_negatives"] == 1
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5) and m["f1"] == pytest.approx(0.5)
    assert evaluate([(preds, gts)], confidence=0.8)["predictions"] == 1
    assert iou_xywh((0, 0, 0.2, 0.2), (0.1, 0, 0.2, 0.2)) == pytest.approx(1 / 3)
