import io

from PIL import Image

from app.catalog import MODEL_CLASSES
from app.seed import device_token_for
from app.services.classifier import analyze
from app.services.detector import MockDetector, RawDetection


def jpeg_bytes(color=(60, 30, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["detector"] == "mock"


def test_login_and_me(client):
    bad = client.post("/api/v1/auth/login", json={"email": "admin@osais.agr.br", "password": "errada"})
    assert bad.status_code == 401
    ok = client.post("/api/v1/auth/login", json={"email": "gestor@osais.agr.br", "password": "osais2026"})
    body = ok.json()
    assert body["user"]["role"] == "gestor"
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['accessToken']}"})
    assert me.json()["email"] == "gestor@osais.agr.br"


def test_sensors_contract(client):
    sensors = client.get("/api/v1/sensors").json()
    assert len(sensors) >= 4
    s = sensors[0]
    # Contrato camelCase idêntico ao frontend
    for key in ("id", "name", "block", "location", "lat", "lng", "status", "varietyId", "battery", "signal", "captureIntervalMin", "lastCommunication"):
        assert key in s
    assert {x["status"] for x in sensors} == {"online", "atencao", "offline"}


def test_readings_history_and_filters(client):
    res = client.get("/api/v1/readings", params={"limit": 50})
    assert res.status_code == 200
    assert int(res.headers["X-Total-Count"]) > 1000
    r = res.json()[0]
    for key in ("id", "sensorId", "capturedAt", "varietyId", "quality", "confidence", "maturation", "classification", "detections", "imageSeed", "stage"):
        assert key in r
    assert r["id"].startswith("OS-")
    syrah = client.get("/api/v1/readings", params={"variety_id": "syrah", "quality": "boa", "limit": 20}).json()
    assert syrah and all(x["varietyId"] == "syrah" and x["quality"] == "boa" for x in syrah)
    search = client.get("/api/v1/readings", params={"q": "Moscato", "limit": 5}).json()
    assert search and all(x["varietyId"] == "moscato-canelli" for x in search)
    one = client.get(f"/api/v1/readings/{r['id']}")
    assert one.json()["id"] == r["id"]


def test_ingest_with_device_token(client):
    files = {"image": ("captura.jpg", jpeg_bytes(), "image/jpeg")}
    denied = client.post("/api/v1/ingest", data={"sensor_id": "S-001"}, files=files, headers={"X-Device-Token": "errado"})
    assert denied.status_code == 401
    res = client.post(
        "/api/v1/ingest",
        data={"sensor_id": "S-001", "battery": "88", "signal": "-60"},
        files={"image": ("captura.jpg", jpeg_bytes(), "image/jpeg")},
        headers={"X-Device-Token": device_token_for("S-001")},
    )
    assert res.status_code == 201, res.text
    reading = res.json()
    assert reading["sensorId"] == "S-001"
    assert reading["varietyId"] == "cabernet-sauvignon"
    assert reading["classification"] in ("APROVADA", "EM OBSERVAÇÃO", "REVISÃO NECESSÁRIA")
    assert reading["imageUrl"].endswith("/image")
    image = client.get(reading["imageUrl"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"


def test_ingest_rejects_non_image(client, admin_headers):
    res = client.post("/api/v1/ingest", data={"sensor_id": "S-002"}, files={"image": ("x.jpg", b"not an image", "image/jpeg")}, headers=admin_headers)
    assert res.status_code == 415


def test_simulate_requires_login_and_skips_offline(client, operador_headers):
    assert client.post("/api/v1/simulate/S-002").status_code == 401
    assert client.post("/api/v1/simulate/S-006", headers=operador_headers).status_code == 409
    res = client.post("/api/v1/simulate/S-002", headers=operador_headers)
    assert res.status_code == 201
    assert res.json()["varietyId"] == "syrah"


def test_heartbeat_updates_status(client):
    res = client.post("/api/v1/sensors/S-004/heartbeat", json={"battery": 18, "signal": -70}, headers={"X-Device-Token": device_token_for("S-004")})
    assert res.status_code == 200
    assert res.json()["status"] == "atencao"
    res = client.post("/api/v1/sensors/S-004/heartbeat", json={"battery": 90, "signal": -60}, headers={"X-Device-Token": device_token_for("S-004")})
    assert res.json()["status"] == "online"


def test_stats(client):
    s = client.get("/api/v1/stats/summary", params={"days": 7}).json()
    assert s["readings"] == s["quality"]["boa"] + s["quality"]["atencao"] + s["quality"]["critica"]
    assert 0 <= s["qualityRatio"] <= 1
    assert s["sensorsActive"] <= s["sensorsTotal"]
    days = client.get("/api/v1/stats/by-day", params={"days": 30}).json()
    assert len(days) >= 25
    varieties = client.get("/api/v1/stats/by-variety").json()
    assert len(varieties) == 6


def test_classifier_rules():
    cluster = RawDetection("chenin_blanc", 0.91, (0.3, 0.15, 0.35, 0.6))
    assert analyze([cluster], None).quality == "boa"
    mild = analyze([cluster, RawDetection("mancha_leve", 0.7, (0.4, 0.3, 0.08, 0.1))], None)
    assert mild.quality == "atencao" and mild.classification == "EM OBSERVAÇÃO"
    severe = analyze([cluster, RawDetection("mancha_leve", 0.7, (0.4, 0.3, 0.08, 0.1)), RawDetection("podridao", 0.8, (0.45, 0.5, 0.08, 0.1))], None)
    assert severe.quality == "critica" and severe.visual_condition == "Sinais de podridão"
    weak = analyze([cluster, RawDetection("podridao", 0.3, (0.45, 0.5, 0.08, 0.1))], None)
    assert weak.quality == "boa"  # anomalia abaixo do limiar de confiança é ignorada
    assert severe.variety_id == "chenin-blanc"


def test_mock_detector_is_deterministic_and_uses_model_classes():
    data = jpeg_bytes((200, 190, 90))
    a = MockDetector().detect(data, "moscato-canelli")
    b = MockDetector().detect(data, "moscato-canelli")
    assert a == b
    assert all(d.label in MODEL_CLASSES for d in a.detections)
    assert all(0 <= v <= 1 for d in a.detections for v in d.box)


def test_public_read_can_be_disabled(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "public_read", False)
    assert client.get("/api/v1/sensors").status_code == 401
