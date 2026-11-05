"""Previsão de variedade pela foto: pré-processamento, votação dos cachos, gravação e migração do banco."""

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, inspect, text

from app.database import ensure_columns
from app.routers import ingest as ingest_router
from app.services.detector import (
    ColorDetector, DetectorOutput, OnnxVarietyClassifier, RawDetection, VarietyPrediction, classify_input, square_crop_box,
)

from .conftest import API
from .images import grape_cluster, jpeg


def test_square_crop_box_is_square_with_margin_and_clipped():
    assert square_crop_box(0.5, 0.5, 0.1, 0.2, 1000, 800) == (404, 304, 596, 496)  # lado = 160 px · 1,2
    x0, y0, x1, y1 = square_crop_box(0.02, 0.5, 0.1, 0.1, 1000, 1000)
    assert x0 == 0 and x1 - x0 < y1 - y0  # recortada na borda esquerda


def test_classify_input_matches_ultralytics_resize_and_center_crop():
    img = Image.new("RGB", (400, 200), (255, 0, 0))
    img.paste((0, 0, 255), (0, 0, 50, 200))  # faixa azul à esquerda, cortada pelo recorte central
    t = classify_input(img, 224)
    assert t.shape == (1, 3, 224, 224) and t.dtype == np.float32 and 0 <= t.min() and t.max() <= 1
    assert t[0, 0, 112, 0] == pytest.approx(1.0) and t[0, 2, 112, 0] == pytest.approx(0.0)  # borda já vermelha


class _FakeClassifier(OnnxVarietyClassifier):
    """Sem ONNX: probabilidades pela cor do recorte (vermelho → syrah, verde → chardonnay)."""

    def __init__(self):
        self.classes = ["chardonnay", "syrah"]

    def probabilities(self, crop):
        r, g, _ = np.asarray(crop.convert("RGB"), dtype=float).mean(axis=(0, 1))
        return np.array([0.9, 0.1]) if g > r else np.array([0.2, 0.8])


def test_votes_are_weighted_by_detection_confidence_and_tiny_clusters_are_ignored():
    img = Image.new("RGB", (1000, 1000), (40, 200, 40))
    img.paste((200, 30, 30), (0, 0, 450, 500))  # região "vermelha" que cobre todo o recorte do 1º cacho
    clusters = [
        RawDetection("cacho", 0.9, (0.1, 0.1, 0.2, 0.3)),  # vermelho, confiança alta
        RawDetection("cacho", 0.3, (0.6, 0.6, 0.2, 0.3)),  # verde, confiança baixa
        RawDetection("cacho", 0.99, (0.9, 0.9, 0.01, 0.01)),  # pequeno demais: não vota
    ]
    pred = _FakeClassifier().predict(img, clusters)
    assert pred.label == "syrah" and pred.clusters == 2
    assert pred.confidence == pytest.approx((0.9 * 0.8 + 0.3 * 0.1) / 1.2, abs=1e-3)
    assert _FakeClassifier().predict(img, [clusters[2]]) is None


class _VarietyDetector(ColorDetector):
    def detect(self, img, variety_hint, variety_type):
        out = super().detect(img, variety_hint, variety_type)
        return DetectorOutput(out.detections, out.model_version, out.notes, VarietyPrediction("chardonnay", 0.87, 3, {"chardonnay": 0.87, "syrah": 0.13}))


def test_predicted_variety_is_stored_next_to_the_plot_variety(client, admin_headers, monkeypatch):
    body = {"id": "VA-01", "name": "Câmera Variedade", "block": "Talhão V", "location": "Fileira 1", "varietyId": "syrah", "device": "ESP32-CAM", "captureIntervalMin": 60}
    assert client.post(f"{API}/sensors", json=body, headers=admin_headers).status_code == 201
    monkeypatch.setattr(ingest_router, "get_detector", lambda: _VarietyDetector())
    res = client.post(f"{API}/ingest", data={"sensor_id": "VA-01"}, files={"image": ("c.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=admin_headers)
    assert res.status_code == 201, res.text
    r = res.json()
    assert r["varietyId"] == "syrah"  # a variedade oficial continua sendo a do talhão
    assert (r["predictedVariety"], r["predictedVarietyName"], r["varietyConfidence"]) == ("chardonnay", "Chardonnay", 0.87)
    again = client.get(f"{API}/readings/{r['id']}", headers=admin_headers).json()
    assert again["predictedVariety"] == "chardonnay"


def test_existing_database_receives_the_new_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/antigo.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE readings (id INTEGER PRIMARY KEY, model_version VARCHAR(80))"))
        conn.execute(text("INSERT INTO readings (id, model_version) VALUES (1, 'OASIS análise de cor v1')"))
    assert ensure_columns(engine) == ["readings.predicted_variety", "readings.variety_confidence"]
    cols = {c["name"] for c in inspect(engine).get_columns("readings")}
    assert {"predicted_variety", "variety_confidence"} <= cols
    assert ensure_columns(engine) == []  # idempotente
    with engine.connect() as conn:
        assert conn.execute(text("SELECT model_version FROM readings")).scalar() == "OASIS análise de cor v1"  # dados preservados
