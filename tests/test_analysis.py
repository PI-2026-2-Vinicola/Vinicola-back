import random

from app.catalog import MODEL_CLASSES
from app.services.classifier import analyze
from app.services.detector import ColorDetector, MockDetector, RawDetection

from .images import BRANCA, grape_cluster

detector = ColorDetector()


def test_color_detector_finds_red_cluster():
    out = detector.detect(grape_cluster(), "syrah", "Tinta")
    assert len(out.detections) == 1 and out.detections[0].label == "syrah"
    x, y, w, h = out.detections[0].box
    assert 0.2 < x + w / 2 < 0.8 and 0.1 < y < 0.4  # caixa sobre o cacho desenhado no centro


def test_color_detector_flags_brown_spots():
    out = detector.detect(grape_cluster(rot=0.3, seed=7), "syrah", "Tinta")
    assert any(d.label in ("podridao", "mancha_leve") for d in out.detections)


def test_color_detector_white_grapes_and_no_cluster():
    assert detector.detect(grape_cluster(BRANCA), "chenin-blanc", "Branca").detections[0].label == "chenin_blanc"
    empty = detector.detect(grape_cluster(cluster=False), "syrah", "Tinta")
    assert empty.detections == [] and empty.notes


def test_maturation_from_berry_color():
    img = grape_cluster()
    out = detector.detect(img, "syrah", "Tinta")
    assert analyze(out.detections, "syrah", "Tinta", img).maturation == "adequada"
    reddish = grape_cluster((200, 60, 70))
    out = detector.detect(reddish, "syrah", "Tinta")
    assert analyze(out.detections, "syrah", "Tinta", reddish).maturation == "pintor"


def test_classifier_rules():
    cluster = RawDetection("syrah", 0.93, (0.3, 0.2, 0.4, 0.6))
    assert analyze([cluster], None).quality == "boa"
    mild = analyze([cluster, RawDetection("mancha_leve", 0.8, (0.4, 0.4, 0.1, 0.1))], None)
    assert mild.quality == "atencao" and mild.classification == "EM OBSERVAÇÃO"
    severe = analyze([cluster, RawDetection("podridao", 0.7, (0.4, 0.4, 0.1, 0.1))], None)
    assert severe.quality == "critica" and severe.classification == "REVISÃO NECESSÁRIA"
    weak = analyze([cluster, RawDetection("podridao", 0.3, (0.4, 0.4, 0.1, 0.1))], None)
    assert weak.quality == "boa"  # anomalia abaixo da confiança mínima é ignorada
    assert analyze([], "syrah").maturation == "nao_informada"


def test_mock_detector_only_uses_model_classes():
    a = MockDetector().simulate(random.Random(3), "syrah", "critica")
    b = MockDetector().simulate(random.Random(3), "syrah", "critica")
    assert a == b and all(d.label in MODEL_CLASSES for d in a.detections)
