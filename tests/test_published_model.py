"""Modelo publicado em models/ (YOLO26n treinado com o Embrapa WGISD) com uma foto real do teste oficial."""

from pathlib import Path

import pytest

from app.services import detector as detector_module
from app.services.detector import BUNDLED_MODEL, OnnxYoloDetector
from app.services.model_card import load_model_card
from ml.evaluation import read_ground_truth
from ml.metrics import iou_xywh

from .conftest import API

pytest.importorskip("onnxruntime")
pytestmark = pytest.mark.skipif(not BUNDLED_MODEL.exists(), reason="modelo ainda não publicado em models/")

FIXTURE = Path(__file__).parent / "fixtures" / "wgisd" / "SYH_2017-04-27_1318.jpg"


@pytest.fixture(scope="module")
def published():
    return OnnxYoloDetector(BUNDLED_MODEL)


def test_published_card_matches_weights_and_describes_real_data():
    card = load_model_card(BUNDLED_MODEL)  # confere o SHA-256 do .onnx
    assert card.task == "deteccao_cachos" and card.classes == ["cacho"]
    assert card.dataset.name == "Embrapa WGISD" and card.dataset.images["test"] == 58 and card.dataset.license == "CC BY-NC 4.0"
    assert {"val", "test"} <= set(card.metrics) and 0 < card.confidence_threshold < 1
    assert card.color_thresholds is not None and card.color_thresholds.report == "ml/reports/calibracao_cor.json"


def test_published_model_finds_the_annotated_clusters_of_a_real_test_photo(published):
    from PIL import Image

    img = Image.open(FIXTURE).convert("RGB")
    found = published.predict(img)
    truth = read_ground_truth(FIXTURE.with_suffix(".txt"))
    matched = sum(1 for g in truth if any(iou_xywh(g, d.box) >= 0.5 for d in found))
    assert len(truth) == 8
    assert matched >= 5, f"{matched} de {len(truth)} cachos anotados encontrados"
    assert len(found) <= len(truth) + 4  # poucas detecções além dos cachos anotados


def test_ingest_with_the_published_model(client, admin_headers, monkeypatch, published):
    body = {"id": "YO-01", "name": "Câmera Syrah", "block": "Talhão Syrah", "location": "Fileira 2", "varietyId": "syrah", "device": "ESP32-CAM", "captureIntervalMin": 60}
    assert client.post(f"{API}/sensors", json=body, headers=admin_headers).status_code == 201
    monkeypatch.setattr("app.routers.ingest.get_detector", lambda: published)
    monkeypatch.setattr("app.routers.integrations.get_detector", lambda: published)
    res = client.post(f"{API}/ingest", data={"sensor_id": "YO-01"}, files={"image": (FIXTURE.name, FIXTURE.read_bytes(), "image/jpeg")}, headers=admin_headers)
    assert res.status_code == 201, res.text
    r = res.json()
    assert r["modelVersion"].startswith("YOLO cachos · OASIS Cachos") and r["varietyId"] == "syrah"
    assert r["clustersDetected"] >= 5 and r["confidence"] >= published.confidence
    assert all(d["label"] == "syrah" for d in r["detections"] if d["kind"] == "cacho")  # variedade do talhão
    info = client.get(f"{API}/analysis/model", headers=admin_headers).json()
    assert info["detector"] == "yolo" and info["card"]["dataset"]["name"] == "Embrapa WGISD"
    assert {c["kind"] for c in info["components"]} == {"modelo", "cadastro", "regra"}
    assert info["classes"][0] == {"name": "cacho", "label": "Cacho de uva", "kind": "cacho", "source": "modelo"}
    assert any(m["split"] == "teste" for m in info["card"]["metrics"])


def test_auto_mode_loads_the_published_model(monkeypatch):
    settings = detector_module.get_settings().model_copy(update={"oasis_detector": "auto", "oasis_model_path": "models/oasis-grapes.pt"})
    monkeypatch.setattr(detector_module, "get_settings", lambda: settings)
    detector = detector_module.get_detector.__wrapped__()
    assert isinstance(detector, OnnxYoloDetector) and detector.path == BUNDLED_MODEL
