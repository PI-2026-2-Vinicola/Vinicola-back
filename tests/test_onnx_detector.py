"""Inferência do modelo YOLO em ONNX: pré-processamento, decodificação da saída, ficha do modelo e seleção do detector."""

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from app.services import detector as detector_module
from app.services.detector import ColorThresholds, decode_yolo_output, letterbox, nms, region_anomalies, resolve_model_path
from app.services.model_card import ModelCardError, load_model_card

from .images import grape_cluster


def test_letterbox_keeps_aspect_ratio_and_pads_like_ultralytics():
    img = Image.new("RGB", (2048, 1365), (10, 200, 30))
    tensor, r, left, top = letterbox(img, 640)
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32
    assert r == pytest.approx(0.3125) and left == 0 and top == 106  # 1365 · 0,3125 = 427 px de altura útil
    assert tensor[0, :, 0, 0] == pytest.approx([114 / 255] * 3)  # faixa de preenchimento cinza
    assert tensor[0, 1, 320, 320] == pytest.approx(200 / 255)  # conteúdo da imagem (canal G)


def test_nms_removes_overlapping_boxes():
    boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [300, 300, 400, 400]], dtype=np.float32)
    keep = nms(boxes, np.array([0.9, 0.8, 0.6]), iou=0.7)
    assert keep == [0, 2]


def test_decode_raw_output_applies_threshold_nms_and_undoes_letterbox():
    # imagem 2048 × 1365 → escala 0,3125, deslocamento vertical 106 px (ver letterbox)
    anchors = np.zeros((5, 8400), dtype=np.float32)
    anchors[:, 0] = [320, 266, 100, 100, 0.92]  # cx, cy, w, h (pixels da entrada), confiança
    anchors[:, 1] = [322, 268, 100, 100, 0.85]  # quase a mesma caixa: removida pelo NMS
    anchors[:, 2] = [100, 200, 40, 60, 0.40]
    anchors[:, 3] = [500, 400, 40, 60, 0.10]  # abaixo do limiar
    dets = decode_yolo_output(anchors[None], ["cacho"], ratio=0.3125, left=0, top=106, image_size=(2048, 1365), confidence=0.25, iou=0.7)
    assert [d.confidence for d in dets] == [0.92, 0.4]
    x, y, w, h = dets[0].box
    assert x == pytest.approx((270 / 0.3125) / 2048, abs=1e-3) and w == pytest.approx((100 / 0.3125) / 2048, abs=1e-3)
    assert y == pytest.approx(((216 - 106) / 0.3125) / 1365, abs=1e-3) and h == pytest.approx((100 / 0.3125) / 1365, abs=1e-3)
    assert all(d.label == "cacho" for d in dets)


def test_decode_end2end_output_and_clipping():
    out = np.zeros((1, 300, 6), dtype=np.float32)
    out[0, 0] = [600, 100, 700, 300, 0.7, 0]  # passa da borda direita da imagem útil
    out[0, 1] = [10, 120, 50, 160, 0.95, 0]
    dets = decode_yolo_output(out, ["cacho"], ratio=0.5, left=0, top=0, image_size=(1280, 1280), confidence=0.25, iou=0.7, end2end=True)
    assert [d.confidence for d in dets] == [0.95, 0.7]
    x, y, w, h = dets[1].box
    assert x + w <= 1.0 and y >= 0 and w > 0 and h > 0


def test_decode_refuses_output_with_other_classes():
    with pytest.raises(ValueError, match="classes"):
        decode_yolo_output(np.zeros((1, 7, 8400), dtype=np.float32), ["cacho"], ratio=1, left=0, top=0, image_size=(640, 640), confidence=0.25, iou=0.7)


def _card(tmp_path, onnx_bytes=b"modelo", **overrides):
    model = tmp_path / "m.onnx"
    model.write_bytes(onnx_bytes)
    card = {
        "name": "Teste", "version": "0.0.1", "task": "deteccao_cachos", "architecture": "YOLO26n", "framework": "teste",
        "input_size": 640, "classes": ["cacho"], "confidence_threshold": 0.3, "iou_threshold": 0.7,
        "trained_at": "2026-11-05T00:00:00+00:00", "training": {}, "metrics": {},
        "dataset": {"name": "Embrapa WGISD", "source_url": "https://github.com/thsant/wgisd", "version": "x", "license": "CC BY-NC 4.0",
                    "institution": "Embrapa", "description": "d", "images": {"total": 1}, "instances": {"total": 1}, "varieties": [], "capture": "c"},
        "sha256": hashlib.sha256(onnx_bytes).hexdigest(), "license": "l", "limitations": [],
    }
    card.update(overrides)
    model.with_suffix(".json").write_text(json.dumps(card))
    return model


def test_model_card_must_match_the_weights_and_the_task(tmp_path):
    assert load_model_card(_card(tmp_path)).classes == ["cacho"]
    with pytest.raises(ModelCardError, match="SHA-256"):
        load_model_card(_card(tmp_path, sha256="0" * 64))
    with pytest.raises(ModelCardError, match="incompatíveis"):
        load_model_card(_card(tmp_path, classes=["person", "car"]))
    with pytest.raises(ModelCardError, match="incompatíveis"):
        load_model_card(_card(tmp_path, task="deteccao_completa", classes=["cacho"]))
    model = _card(tmp_path)
    model.with_suffix(".json").unlink()
    with pytest.raises(ModelCardError, match="não encontrada"):
        load_model_card(model)


def test_color_rules_inside_a_detected_cluster_use_the_given_thresholds():
    img = grape_cluster(rot=0.3, seed=7)
    box = (0.3, 0.12, 0.4, 0.62)  # região do cacho desenhado (ver tests/images.py)
    strict = region_anomalies(img, box, "Tinta", ColorThresholds(brown_mild=0.9, brown_severe=0.95, mold=0.9, off_color=0.9))
    assert strict == []
    lenient = region_anomalies(img, box, "Tinta", ColorThresholds(brown_mild=0.01, brown_severe=0.9, mold=0.9, off_color=0.9))
    assert [a.label for a in lenient] == ["mancha_leve"]
    x, y, w, h = lenient[0].box
    assert box[0] <= x and x + w <= box[0] + box[2] + 1e-6 and box[1] <= y and y + h <= box[1] + box[3] + 1e-6  # dentro do cacho
    assert region_anomalies(img, (0.5, 0.5, 0.001, 0.001), "Tinta") == []  # caixa pequena demais


def test_legacy_env_path_falls_back_to_published_model_only(monkeypatch, tmp_path):
    bundled = tmp_path / "oasis-cachos.onnx"
    bundled.write_bytes(b"x")
    monkeypatch.setattr(detector_module, "BUNDLED_MODEL", bundled)
    assert resolve_model_path("models/oasis-grapes.pt") == bundled  # .env antigo
    assert resolve_model_path(str(tmp_path / "outro.onnx")) == tmp_path / "outro.onnx"  # caminho explícito é respeitado
