from .conftest import API


def test_create_sensor_returns_token_once(client, sensor):
    data, token = sensor
    assert token.startswith("oasis_")
    assert data["status"] == "offline" and "comunicação" in data["statusReason"]
    assert data["hasValidLocation"] is True and data["analysesCount"] == 0


def test_sensor_contract(client, admin_headers, sensor):
    s = client.get(f"{API}/sensors/T-001", headers=admin_headers).json()
    for key in ("id", "name", "block", "location", "lat", "lng", "hasValidLocation", "varietyId", "battery", "signal",
                "captureIntervalMin", "active", "status", "statusReason", "lastCommunication", "analysesCount", "lastReadingAt", "qualityRatio"):
        assert key in s


def test_sensor_validation_and_permissions(client, admin_headers, operador_headers):
    base = {"id": "T-099", "name": "Teste", "block": "B", "location": "L", "varietyId": "syrah"}
    assert client.post(f"{API}/sensors", json=base, headers=operador_headers).status_code == 403
    assert client.post(f"{API}/sensors", json={**base, "lat": 0, "lng": 0}, headers=admin_headers).status_code == 422
    assert client.post(f"{API}/sensors", json={**base, "lat": -9.1}, headers=admin_headers).status_code == 422
    assert client.post(f"{API}/sensors", json={**base, "id": "x"}, headers=admin_headers).status_code == 422
    assert client.post(f"{API}/sensors", json={**base, "varietyId": "merlot"}, headers=admin_headers).status_code == 422
    assert client.post(f"{API}/sensors", json={**base, "id": "T-001"}, headers=admin_headers).status_code == 409


def test_heartbeat_with_device_token(client, admin_headers, sensor):
    _, token = sensor
    assert client.post(f"{API}/sensors/T-001/heartbeat", json={"battery": 90}, headers={"X-Device-Token": "errado"}).status_code == 401
    res = client.post(f"{API}/sensors/T-001/heartbeat", json={"battery": 88, "signal": -60, "firmware": "v2.0.0", "temperatureC": 29.5, "humidityPct": 48}, headers={"X-Device-Token": token})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "online" and body["battery"] == 88 and body["firmware"] == "v2.0.0"
    telemetry = client.get(f"{API}/sensors/T-001/telemetry", headers=admin_headers).json()
    assert telemetry[-1]["battery"] == 88
    env = client.get(f"{API}/sensors/T-001/environment", headers=admin_headers).json()
    assert env[-1]["temperatureC"] == 29.5 and env[-1]["source"] == "sensor"


def test_low_battery_turns_attention(client, admin_headers):
    res = client.post(f"{API}/sensors", json={"id": "T-002", "name": "Câmera 2", "block": "B", "location": "L", "varietyId": "chenin-blanc"}, headers=admin_headers)
    token = res.json()["deviceToken"]
    body = client.post(f"{API}/sensors/T-002/heartbeat", json={"battery": 12}, headers={"X-Device-Token": token}).json()
    assert body["status"] == "atencao" and "bateria" in body["statusReason"].lower()
    assert body["hasValidLocation"] is False


def test_environment_manual_entry(client, admin_headers, sensor):
    res = client.post(f"{API}/sensors/T-001/environment", json={"measuredAt": "2026-01-10T10:00:00-03:00", "temperatureC": 31.2, "luminosityLux": 52000}, headers=admin_headers)
    assert res.status_code == 201
    dup = client.post(f"{API}/sensors/T-001/environment", json={"measuredAt": "2026-01-10T10:00:00-03:00", "temperatureC": 30}, headers=admin_headers)
    assert dup.status_code == 409
    empty = client.post(f"{API}/sensors/T-001/environment", json={"measuredAt": "2026-01-11T10:00:00-03:00"}, headers=admin_headers)
    assert empty.status_code == 409
    history = client.get(f"{API}/sensors/T-001/environment", params={"dateFrom": "2026-01-10", "dateTo": "2026-01-10"}, headers=admin_headers).json()
    assert len(history) == 1 and history[0]["luminosityLux"] == 52000


def test_update_location_and_deactivate(client, admin_headers):
    res = client.patch(f"{API}/sensors/T-002", json={"lat": -9.4, "lng": -40.6}, headers=admin_headers)
    assert res.json()["hasValidLocation"] is True
    res = client.patch(f"{API}/sensors/T-002", json={"clearLocation": True}, headers=admin_headers)
    assert res.json()["lat"] is None and res.json()["hasValidLocation"] is False
    res = client.patch(f"{API}/sensors/T-002", json={"active": False}, headers=admin_headers)
    assert res.json()["status"] == "inativo"


def test_regenerate_token_invalidates_previous(client, admin_headers):
    res = client.post(f"{API}/sensors", json={"id": "T-003", "name": "Câmera 3", "block": "B", "location": "L", "varietyId": "syrah"}, headers=admin_headers)
    old = res.json()["deviceToken"]
    new = client.post(f"{API}/sensors/T-003/token", headers=admin_headers).json()["deviceToken"]
    assert client.post(f"{API}/sensors/T-003/heartbeat", json={}, headers={"X-Device-Token": old}).status_code == 401
    assert client.post(f"{API}/sensors/T-003/heartbeat", json={}, headers={"X-Device-Token": new}).status_code == 200
