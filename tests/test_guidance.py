"""
Detecções do modelo (YOLO ou análise de cor) cruzadas com o livro de uva do RespondeAgro.
A AgroAPI é simulada com o formato da documentação oficial (ver test_embrapa.py).
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.database import SessionLocal
from app.models import Sensor
from app.services.classifier import analyze
from app.services.detector import RawDetection
from app.services.embrapa import strongest_anomalies
from app.services.readings import persist_reading

from .conftest import API
from .test_embrapa import SEARCH, FakeAgroAPI, embrapa_override  # noqa: F401

SEARCH_PATH = "/respondeagro/v1/_search/template"


def make_reading(sensor_id: str, detections: list[RawDetection], minute: int) -> str:
    with SessionLocal() as db:
        sensor = db.get(Sensor, sensor_id)
        reading = persist_reading(
            db, sensor=sensor, analysis=analyze(detections, sensor.variety_id), detections=detections,
            captured_at=datetime(2026, 1, 1, 12, minute, tzinfo=timezone.utc), model_version="yolo-teste", processing_ms=1, source="upload",
        )
        return reading.code


CLUSTER = RawDetection("syrah", 0.92, (0.2, 0.2, 0.5, 0.6))


def test_strongest_anomalies_dedupes_and_puts_severe_first():
    found = [("mancha_leve", 0.9), ("podridao", 0.6), ("mancha_leve", 0.7), ("podridao", 0.8), ("lesao", 0.4), ("syrah", 0.99), ("desconhecida", 0.9)]
    assert strongest_anomalies(found, 0.5) == [("podridao", 0.8), ("mancha_leve", 0.9)]


def test_reading_guidance_queries_grape_book_per_anomaly(client, admin_headers, sensor, embrapa_override):
    code = make_reading(sensor[0]["id"], [CLUSTER, RawDetection("mancha_leve", 0.7, (0.3, 0.3, 0.1, 0.1)), RawDetection("podridao", 0.85, (0.4, 0.4, 0.1, 0.1))], 1)
    api = FakeAgroAPI()
    api.routes[("POST", SEARCH_PATH)] = httpx.Response(200, json=SEARCH)
    embrapa_override(api)

    res = client.get(f"{API}/readings/{code}/embrapa", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True and body["readingId"] == code and body["modelVersion"] == "yolo-teste" and body["fetchedAt"]
    assert [a["anomaly"] for a in body["anomalies"]] == ["podridao", "mancha_leve"]
    first = body["anomalies"][0]
    assert first["severe"] is True and first["label"] == "Sinais de podridão" and first["total"] == 24 and len(first["items"]) == 2
    sent = [json.loads(c.content) for c in api.api_calls(SEARCH_PATH)]
    assert [s["params"]["query_string"] for s in sent] == ["podridão do cacho", "manchas nas bagas"]
    assert all(s["id"] == "query_one_book" and s["params"]["book_id"] == "uva" and s["params"]["size"] == 3 for s in sent)


def test_reading_without_anomalies_makes_no_external_call(client, admin_headers, sensor, embrapa_override):
    code = make_reading(sensor[0]["id"], [CLUSTER], 2)
    api = FakeAgroAPI()
    embrapa_override(api)
    body = client.get(f"{API}/readings/{code}/embrapa", headers=admin_headers).json()
    assert body["anomalies"] == [] and "Nenhuma anomalia" in body["detail"] and api.calls == []


def test_reading_guidance_without_credentials_invents_nothing(client, admin_headers, sensor, embrapa_override):
    code = make_reading(sensor[0]["id"], [CLUSTER, RawDetection("lesao", 0.9, (0.3, 0.3, 0.1, 0.1))], 3)
    api = FakeAgroAPI()
    embrapa_override(api, embrapa_consumer_key="", embrapa_consumer_secret="")
    body = client.get(f"{API}/readings/{code}/embrapa", headers=admin_headers).json()
    assert body["configured"] is False and body["anomalies"] == [] and "EMBRAPA_CONSUMER_KEY" in body["detail"] and api.calls == []


@pytest.mark.parametrize("path", ["/readings/OA-99999/embrapa"])
def test_reading_guidance_requires_login_and_existing_reading(client, admin_headers, embrapa_override, path):
    embrapa_override(FakeAgroAPI())
    assert client.get(f"{API}{path}").status_code == 401
    assert client.get(f"{API}{path}", headers=admin_headers).status_code == 404
