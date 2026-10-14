"""Modelo de análise em uso, proteção contra modelos genéricos e limites da análise de imagens."""

import time
from pathlib import Path

import pytest

from app.config import get_settings
from app.routers import ingest as ingest_router
from app.services import detector as detector_module
from app.services.detector import ColorDetector, DetectorOutput, check_model_classes, to_raw_detections

from .conftest import API
from .images import grape_cluster, jpeg


def test_model_info_requires_login_and_describes_color_detector(client, operador_headers):
    assert client.get(f"{API}/analysis/model").status_code == 401
    info = client.get(f"{API}/analysis/model", headers=operador_headers).json()
    assert info["detector"] == "color" and info["yoloLoaded"] is False and info["confidenceThreshold"] is None
    assert info["yoloWeightsFile"] == "oasis-grapes.pt" and info["yoloWeightsFound"] is False
    kinds = {c["name"]: c["kind"] for c in info["classes"]}
    assert kinds["syrah"] == "variedade" and kinds["mancha_leve"] == "anomalia_leve" and kinds["podridao"] == "anomalia_grave"
    assert "lesao" not in kinds  # a análise de cor não detecta lesões: só as classes que o detector produz
    assert info["maxUploadMb"] == 8 and info["acceptedFormats"] == ["JPEG", "PNG", "WEBP"] and info["timeoutSeconds"] == 30
    assert any("Nenhum modelo YOLO treinado" in t for t in info["limitations"])
    assert any("não substitui" in t for t in info["limitations"])


def test_generic_model_is_refused_and_partial_model_ignores_unknown_classes():
    with pytest.raises(ValueError, match="modelo genérico"):
        check_model_classes(["person", "bicycle", "car"], "yolov8n.pt")
    assert check_model_classes(["syrah", "podridao", "folha"], "parcial.pt") == ["folha"]


def test_yolo_boxes_are_converted_clamped_and_filtered():
    names = {0: "syrah", 1: "person", 2: "podridao"}
    dets, ignored = to_raw_detections(
        [[0.5, 0.5, 0.4, 0.6], [0.2, 0.2, 0.1, 0.1], [0.98, 0.05, 0.1, 0.2]], [0.91, 0.88, 0.7], [0, 1, 2], names
    )
    assert ignored == ["person"]
    assert [(d.label, d.confidence) for d in dets] == [("syrah", 0.91), ("podridao", 0.7)]
    assert dets[0].box == (0.3, 0.2, 0.4, 0.6)  # centro → canto superior esquerdo
    x, y, w, h = dets[1].box
    assert y == 0.0 and x + w <= 1.0 and h == pytest.approx(0.15)  # caixa mantida dentro da imagem


def test_auto_mode_falls_back_to_color_when_weights_are_not_an_oasis_model(monkeypatch, tmp_path):
    weights = tmp_path / "oasis-grapes.pt"
    weights.write_bytes(b"pesos")

    class GenericYolo:
        def __init__(self, path, confidence):
            check_model_classes(["person", "car"], Path(path).name)

    settings = get_settings().model_copy(update={"oasis_model_path": str(weights), "oasis_detector": "auto"})
    monkeypatch.setattr(detector_module, "get_settings", lambda: settings)
    monkeypatch.setattr(detector_module, "YoloDetector", GenericYolo)
    assert isinstance(detector_module.get_detector.__wrapped__(), ColorDetector)

    strict = settings.model_copy(update={"oasis_detector": "yolo"})
    monkeypatch.setattr(detector_module, "get_settings", lambda: strict)
    with pytest.raises(ValueError):
        detector_module.get_detector.__wrapped__()


class SlowDetector(ColorDetector):
    def detect(self, img, variety_hint, variety_type):
        time.sleep(1.5)
        return DetectorOutput([], self.version)


class BrokenDetector(ColorDetector):
    def detect(self, img, variety_hint, variety_type):
        raise RuntimeError("falha no modelo")


def _upload(client, headers, at):
    return client.post(f"{API}/ingest", data={"sensor_id": "T-001", "captured_at": at}, files={"image": ("c.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=headers)


def test_analysis_timeout_returns_504(client, operador_headers, sensor, monkeypatch):
    settings = get_settings().model_copy(update={"oasis_analysis_timeout_seconds": 0.2})
    monkeypatch.setattr(ingest_router, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest_router, "get_detector", lambda: SlowDetector())
    res = _upload(client, operador_headers, "2026-09-22T08:00:00-03:00")
    assert res.status_code == 504 and "tempo limite" in res.json()["detail"]


def test_detector_failure_returns_503_without_saving(client, operador_headers, admin_headers, sensor, monkeypatch):
    monkeypatch.setattr(ingest_router, "get_detector", lambda: BrokenDetector())
    res = _upload(client, operador_headers, "2026-09-22T09:00:00-03:00")
    assert res.status_code == 503 and "Tente novamente" in res.json()["detail"]
    readings = client.get(f"{API}/readings", params={"sensorId": "T-001", "dateFrom": "2026-09-22", "dateTo": "2026-09-22"}, headers=admin_headers).json()
    assert readings["total"] == 0
