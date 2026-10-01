import csv
import io

from PIL import Image

from .conftest import API
from .images import BRANCA, grape_cluster, jpeg


def _ingest(client, headers, image: bytes, sensor_id="T-001", **form):
    return client.post(f"{API}/ingest", data={"sensor_id": sensor_id, **form}, files={"image": ("cacho.jpg", image, "image/jpeg")}, headers=headers)


def test_device_ingest_analyzes_real_image(client, admin_headers, sensor):
    _, token = sensor
    res = _ingest(client, {"X-Device-Token": token}, jpeg(grape_cluster()), captured_at="2026-09-20T09:00:00-03:00", battery="77", temperature_c="27.4")
    assert res.status_code == 201, res.text
    r = res.json()
    assert r["id"].startswith("OA-") and r["source"] == "sensor"
    assert r["quality"] == "boa" and r["classification"] == "APROVADA"
    assert r["clustersDetected"] == 1 and r["varietyId"] == "syrah"
    assert r["detections"][0]["kind"] == "cacho" and 0 < r["confidence"] <= 1
    assert r["modelVersion"].startswith("OASIS análise de cor")
    assert r["imageUrl"].endswith("/image") and r["thumbUrl"].endswith("size=thumb")
    sensor_now = client.get(f"{API}/sensors/T-001", headers=admin_headers).json()
    assert sensor_now["battery"] == 77 and sensor_now["analysesCount"] >= 1


def test_image_and_thumbnail_are_served_without_exif(client, admin_headers):
    code = client.get(f"{API}/readings", params={"sensorId": "T-001"}, headers=admin_headers).json()["items"][0]["id"]
    full = client.get(f"{API}/readings/{code}/image", headers=admin_headers)
    thumb = client.get(f"{API}/readings/{code}/image", params={"size": "thumb"}, headers=admin_headers)
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(thumb.content)).size) <= 360
    assert not Image.open(io.BytesIO(full.content)).getexif()
    assert client.get(f"{API}/readings/{code}/image").status_code == 401


def test_user_upload_detects_rot(client, operador_headers, sensor):
    res = _ingest(client, operador_headers, jpeg(grape_cluster(rot=0.3, seed=4)), captured_at="2026-09-21T10:00:00-03:00")
    assert res.status_code == 201
    r = res.json()
    assert r["source"] == "upload" and r["quality"] == "critica"
    assert any(d["kind"] == "anomalia" for d in r["detections"])


def test_upload_without_cluster(client, operador_headers, sensor):
    res = _ingest(client, operador_headers, jpeg(grape_cluster(cluster=False)), captured_at="2026-09-21T11:00:00-03:00")
    r = res.json()
    assert r["clustersDetected"] == 0 and r["visualCondition"] == "Cacho não identificado" and r["maturation"] == "nao_informada"


def test_white_grape_sensor(client, admin_headers):
    token = client.post(f"{API}/sensors", json={"id": "T-010", "name": "Câmera Branca", "block": "Bloco C", "location": "L", "varietyId": "chenin-blanc"}, headers=admin_headers).json()["deviceToken"]
    r = _ingest(client, {"X-Device-Token": token}, jpeg(grape_cluster(BRANCA)), sensor_id="T-010").json()
    assert r["varietyId"] == "chenin-blanc" and r["quality"] == "boa"


def test_ingest_rejections(client, admin_headers, operador_headers, sensor):
    _, token = sensor
    assert _ingest(client, {"X-Device-Token": "invalido"}, jpeg(grape_cluster())).status_code == 401
    assert _ingest(client, {}, jpeg(grape_cluster())).status_code == 401
    assert _ingest(client, operador_headers, b"nao sou imagem").status_code == 415
    gif = io.BytesIO()
    Image.new("RGB", (50, 50)).save(gif, "GIF")
    assert _ingest(client, operador_headers, gif.getvalue()).status_code == 415
    assert _ingest(client, operador_headers, jpeg(grape_cluster()), captured_at="2099-01-01T00:00:00Z").status_code == 422
    dup = _ingest(client, {"X-Device-Token": token}, jpeg(grape_cluster()), captured_at="2026-09-20T09:00:00-03:00")
    assert dup.status_code == 409
    assert _ingest(client, operador_headers, jpeg(grape_cluster()), sensor_id="NAO-EXISTE").status_code == 404


def test_inactive_sensor_rejects_ingest(client, admin_headers, operador_headers):
    client.post(f"{API}/sensors", json={"id": "T-020", "name": "Câmera inativa", "block": "B", "location": "L", "varietyId": "syrah"}, headers=admin_headers)
    client.patch(f"{API}/sensors/T-020", json={"active": False}, headers=admin_headers)
    assert _ingest(client, operador_headers, jpeg(grape_cluster()), sensor_id="T-020").status_code == 409


def test_readings_pagination_and_filters(client, admin_headers):
    page = client.get(f"{API}/readings", params={"pageSize": 2}, headers=admin_headers).json()
    assert page["total"] >= 4 and len(page["items"]) == 2 and page["page"] == 1
    crit = client.get(f"{API}/readings", params={"quality": "critica"}, headers=admin_headers).json()
    assert crit["total"] >= 1 and all(r["quality"] == "critica" for r in crit["items"])
    multi = client.get(f"{API}/readings", params={"quality": "boa,critica"}, headers=admin_headers).json()
    assert all(r["quality"] in ("boa", "critica") for r in multi["items"])
    by_day = client.get(f"{API}/readings", params={"dateFrom": "2026-09-21", "dateTo": "2026-09-21"}, headers=admin_headers).json()
    assert by_day["total"] == 2
    by_variety = client.get(f"{API}/readings", params={"varietyId": "chenin-blanc"}, headers=admin_headers).json()
    assert all(r["varietyId"] == "chenin-blanc" for r in by_variety["items"]) and by_variety["total"] >= 1
    by_block = client.get(f"{API}/readings", params={"block": "Bloco C"}, headers=admin_headers).json()
    assert by_block["total"] >= 1 and all(r["block"] == "Bloco C" for r in by_block["items"])
    search = client.get(f"{API}/readings", params={"q": "podridão"}, headers=admin_headers).json()
    assert search["total"] >= 1
    wildcard = client.get(f"{API}/readings", params={"q": "%"}, headers=admin_headers).json()
    assert wildcard["total"] == 0
    assert client.get(f"{API}/readings", params={"quality": "otima"}, headers=admin_headers).status_code == 422
    assert client.get(f"{API}/readings", params={"dateFrom": "2026-09-22", "dateTo": "2026-09-01"}, headers=admin_headers).status_code == 422
    oldest = client.get(f"{API}/readings", params={"sort": "antigas", "pageSize": 1}, headers=admin_headers).json()["items"][0]
    newest = client.get(f"{API}/readings", params={"sort": "recentes", "pageSize": 1}, headers=admin_headers).json()["items"][0]
    assert oldest["capturedAt"] <= newest["capturedAt"]


def test_export_csv(client, admin_headers):
    res = client.get(f"{API}/readings/export", params={"sensorId": "T-001"}, headers=admin_headers)
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    text = res.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][0] == "codigo" and len(rows) >= 2


def test_reading_detail_and_delete(client, admin_headers, operador_headers):
    code = client.get(f"{API}/readings", params={"sensorId": "T-010"}, headers=admin_headers).json()["items"][0]["id"]
    assert client.get(f"{API}/readings/{code}", headers=operador_headers).json()["id"] == code
    assert client.delete(f"{API}/readings/{code}", headers=operador_headers).status_code == 403
    assert client.delete(f"{API}/readings/{code}", headers=admin_headers).status_code == 204
    assert client.get(f"{API}/readings/{code}", headers=admin_headers).status_code == 404
    assert client.get(f"{API}/readings/OA-99999", headers=admin_headers).status_code == 404
