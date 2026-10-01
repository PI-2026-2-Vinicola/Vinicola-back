from .conftest import API
from .images import grape_cluster, jpeg


def _seed(client, admin_headers):
    """Cenário próprio: sensor e leituras reais em dias conhecidos."""
    if client.get(f"{API}/sensors/ST-01", headers=admin_headers).status_code == 404:
        token = client.post(f"{API}/sensors", json={"id": "ST-01", "name": "Indicadores", "block": "Bloco Stats", "location": "L", "varietyId": "tempranillo"}, headers=admin_headers).json()["deviceToken"]
        h = {"X-Device-Token": token}
        for at, rot in (("2026-09-25T08:00:00-03:00", 0), ("2026-09-25T14:00:00-03:00", 0.3), ("2026-09-27T09:30:00-03:00", 0)):
            res = client.post(f"{API}/ingest", data={"sensor_id": "ST-01", "captured_at": at, "temperature_c": "30"}, files={"image": ("c.jpg", jpeg(grape_cluster(rot=rot)), "image/jpeg")}, headers=h)
            assert res.status_code == 201


def test_summary(client, admin_headers):
    _seed(client, admin_headers)
    s = client.get(f"{API}/stats/summary", params={"sensorId": "ST-01", "dateFrom": "2026-09-25", "dateTo": "2026-09-27"}, headers=admin_headers).json()
    assert s["readings"] == 3 and s["quality"]["boa"] == 2 and s["quality"]["critica"] == 1 and s["alerts"] == 1
    assert s["qualityRatio"] == round(2 / 3, 4) and s["sensorsTotal"] == 1 and s["sensorsOnline"] == 1
    assert s["previous"]["readings"] == 0 and s["environment"]["measurements"] == 3 and s["environment"]["avgTemperatureC"] == 30
    empty = client.get(f"{API}/stats/summary", params={"sensorId": "ST-01", "dateFrom": "2026-01-01", "dateTo": "2026-01-02"}, headers=admin_headers).json()
    assert empty["readings"] == 0 and empty["qualityRatio"] is None and empty["avgConfidence"] is None


def test_by_day_fills_empty_days_in_local_time(client, admin_headers):
    _seed(client, admin_headers)
    days = client.get(f"{API}/stats/by-day", params={"sensorId": "ST-01", "dateFrom": "2026-09-24", "dateTo": "2026-09-28"}, headers=admin_headers).json()
    assert [d["key"] for d in days] == ["2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"]
    assert [d["total"] for d in days] == [0, 2, 0, 1, 0] and days[1]["label"] == "25/09"
    assert days[0]["avgConfidence"] is None


def test_by_hour_and_breakdown(client, admin_headers):
    _seed(client, admin_headers)
    params = {"sensorId": "ST-01", "dateFrom": "2026-09-25", "dateTo": "2026-09-27"}
    hours = client.get(f"{API}/stats/by-hour", params=params, headers=admin_headers).json()
    assert len(hours) == 24 and hours[8]["total"] == 1 and hours[14]["total"] == 1 and hours[9]["total"] == 1
    varieties = client.get(f"{API}/stats/breakdown", params={**params, "by": "variety"}, headers=admin_headers).json()
    assert varieties == [{"key": "tempranillo", "total": 3, "boa": 2, "atencao": 0, "critica": 1, "avgConfidence": varieties[0]["avgConfidence"]}]
    blocks = client.get(f"{API}/stats/breakdown", params={"by": "block", "days": 3660}, headers=admin_headers).json()
    assert any(b["key"] == "Bloco Stats" for b in blocks)
    assert client.get(f"{API}/stats/breakdown", params={"by": "senha"}, headers=admin_headers).status_code == 422


def test_public_overview_has_only_aggregates(client, admin_headers):
    _seed(client, admin_headers)
    o = client.get(f"{API}/public/overview").json()
    assert o["sensorsTotal"] >= 1 and o["analyses30d"] >= 3 and o["varietiesMonitored"] >= 1
    assert "lat" not in str(o)


def test_system_status_and_audit(client, admin_headers, gestor_headers):
    assert client.get(f"{API}/system/status", headers=gestor_headers).status_code == 403
    st = client.get(f"{API}/system/status", headers=admin_headers).json()
    assert st["detector"] == "color" and st["database"] in ("sqlite", "postgresql", "mysql") and st["counts"]["usuarios"] >= 2
    audit = client.get(f"{API}/audit", headers=admin_headers).json()
    actions = {a["action"] for a in audit["items"]}
    assert {"login", "sensor_criado"} <= actions and audit["total"] >= len(audit["items"])
    logins = client.get(f"{API}/audit", params={"action": "login", "pageSize": 1}, headers=admin_headers).json()
    assert logins["pageSize"] == 1 and logins["items"][0]["action"] == "login"
