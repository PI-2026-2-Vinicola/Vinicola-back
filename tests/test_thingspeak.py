"""Integração com o ThingSpeak — respostas simuladas no formato documentado (readdata) e erros."""

from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.main import app
from app.services import external
from app.services.external import ExternalServiceError
from app.services.thingspeak import ThingSpeakService, build_feed, get_thingspeak, parse_number, parse_units

from .conftest import API

CHANNEL = {
    "id": 4242, "name": "Vinhedo Teste", "description": "Estação do talhão 3", "latitude": "-9.39", "longitude": "-40.5",
    "field1": "Temperatura do ar", "field2": "Umidade do ar", "field3": "Status", "created_at": "2026-01-10T12:00:00Z",
    "updated_at": "2026-10-01T08:00:00Z", "last_entry_id": 3,
}
FEEDS = [
    {"created_at": "2026-10-14T10:02:00Z", "entry_id": 3, "field1": "31.2", "field2": "", "field3": "ok"},
    {"created_at": "2026-10-14T10:00:00Z", "entry_id": 1, "field1": "30,5", "field2": "48", "field3": "ok"},
    {"created_at": "2026-10-14T10:01:00Z", "entry_id": 2, "field1": "nan", "field2": "47.5", "field3": "reiniciando"},
]


def payload(feeds=FEEDS):
    return {"channel": CHANNEL, "feeds": feeds}


def make_service(handler, **overrides) -> tuple[ThingSpeakService, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    settings = Settings(_env_file=None, **{"thingspeak_channel_id": "4242", **overrides})
    return ThingSpeakService(settings, transport=httpx.MockTransport(record)), calls


def ok(_request):
    return httpx.Response(200, json=payload())


def test_parse_number_rejects_text_nan_and_accepts_decimal_comma():
    assert parse_number("31.2") == 31.2 and parse_number(" 30,5 ") == 30.5 and parse_number("-2") == -2.0 and parse_number("1e3") == 1000.0
    for raw in ("", None, "nan", "inf", "ok", "1,234.5", True):
        assert parse_number(raw) is None
    assert parse_units("field1=°C, field2=% ;field9=x;bad") == {"field1": "°C", "field2": "%"}


def test_build_feed_uses_only_channel_fields_and_converts_values():
    feed = build_feed(payload(), channel_id="4242", period="recent", aggregation="x", units={"field1": "°C"}, refresh_seconds=60, fetched_at=datetime.now(timezone.utc))
    assert [f.key for f in feed.fields] == ["field1", "field2", "field3"]  # field4…8 não existem no canal
    assert [e.entry_id for e in feed.entries] == [1, 2, 3]  # ordenado pela data
    temp, hum, status = feed.fields
    assert temp.name == "Temperatura do ar" and temp.unit == "°C" and temp.numeric
    assert temp.latest == 31.2 and temp.count == 2 and temp.min == 30.5 and temp.max == 31.2 and temp.avg == pytest.approx(30.85)
    assert feed.entries[1].values["field1"] is None  # "nan" não vira número
    assert hum.latest == 47.5 and hum.latest_at == datetime(2026, 10, 14, 10, 1, tzinfo=timezone.utc)  # último valor preenchido
    assert hum.unit is None  # sem unidade inventada
    assert not status.numeric and status.latest is None and status.latest_text == "ok"
    assert feed.channel.latitude == -9.39 and feed.last_entry_at == datetime(2026, 10, 14, 10, 2, tzinfo=timezone.utc)


def test_build_feed_rejects_unexpected_format():
    with pytest.raises(ExternalServiceError) as exc:
        build_feed({"feeds": []}, channel_id="1", period="recent", aggregation="", units={}, refresh_seconds=60, fetched_at=datetime.now(timezone.utc))
    assert exc.value.status_code == 502


def test_not_configured_does_not_call_thingspeak():
    svc, calls = make_service(ok, thingspeak_channel_id="")
    feed = svc.feed()
    assert feed.configured is False and feed.entries == [] and calls == []


def test_recent_and_aggregated_periods_send_documented_parameters():
    svc, calls = make_service(ok, thingspeak_read_api_key="CHAVE-LEITURA")
    svc.feed("recent", 50)
    svc.feed("7d")
    recent, week = (c.url for c in calls)
    assert recent.path == "/channels/4242/feeds.json" and recent.params["results"] == "50" and recent.params["api_key"] == "CHAVE-LEITURA"
    assert week.params["days"] == "7" and week.params["timescale"] == "60" and "results" not in week.params


def test_responses_are_cached():
    svc, calls = make_service(ok)
    first = svc.feed()
    assert svc.feed() is first and len(calls) == 1


@pytest.mark.parametrize(
    ("response", "status", "fragment"),
    [
        (httpx.Response(200, text="-1"), 502, "recusou o acesso"),
        (httpx.Response(400, text="-1"), 502, "THINGSPEAK_READ_API_KEY"),
        (httpx.Response(404, json={"status": "404", "error": "Not Found"}), 502, "não encontrado"),
        (httpx.Response(429, json={"error_code": "error_too_many_requests"}), 503, "limitou"),
        (httpx.Response(503), 503, "erro 503"),
        (httpx.Response(200, text="<html>"), 502, "JSON"),
    ],
)
def test_http_errors_are_translated(response, status, fragment):
    svc, _ = make_service(lambda _r: response)
    with pytest.raises(ExternalServiceError) as exc:
        svc.feed()
    assert exc.value.status_code == status and fragment in exc.value.message


@pytest.mark.parametrize(("error", "status"), [(httpx.ReadTimeout("t"), 504), (httpx.ConnectError("c"), 503)])
def test_network_failures(error, status):
    def fail(_request):
        raise error

    svc, _ = make_service(fail)
    with pytest.raises(ExternalServiceError) as exc:
        svc.feed()
    assert exc.value.status_code == status


def test_empty_channel_has_fields_without_values():
    svc, _ = make_service(lambda _r: httpx.Response(200, json=payload(feeds=[])))
    feed = svc.feed()
    assert feed.entries == [] and feed.last_entry_at is None
    assert all(f.latest is None and f.count == 0 for f in feed.fields)


def test_last_valid_response_is_served_when_thingspeak_fails(monkeypatch):
    responses = iter([httpx.Response(200, json=payload()), httpx.Response(502)])
    svc, _ = make_service(lambda _r: next(responses))
    fresh = svc.feed()
    now = external.time.monotonic()
    monkeypatch.setattr(external.time, "monotonic", lambda: now + 120)  # cache vencido
    stale = svc.feed()
    assert stale.stale and "502" in stale.warning and stale.entries == fresh.entries


# ------------------------------------------------------------------ rota da API
@pytest.fixture
def thingspeak_override():
    def use(handler, **overrides):
        svc, _ = make_service(handler, **overrides)
        app.dependency_overrides[get_thingspeak] = lambda: svc
        return svc

    yield use
    app.dependency_overrides.pop(get_thingspeak, None)


def test_route_requires_login_and_returns_camel_case(client, operador_headers, thingspeak_override):
    thingspeak_override(ok)
    assert client.get(f"{API}/iot/thingspeak").status_code == 401
    res = client.get(f"{API}/iot/thingspeak", headers=operador_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] and body["channelId"] == "4242" and body["refreshSeconds"] == 60
    assert body["fields"][0]["latestAt"] == "2026-10-14T10:02:00Z" and body["entries"][0]["values"]["field1"] == 30.5
    assert client.get(f"{API}/iot/thingspeak", params={"results": 5}, headers=operador_headers).status_code == 422
    assert client.get(f"{API}/iot/thingspeak", params={"period": "1ano"}, headers=operador_headers).status_code == 422


def test_route_reports_upstream_errors_in_portuguese(client, operador_headers, thingspeak_override):
    thingspeak_override(lambda _r: httpx.Response(200, text="-1"))
    res = client.get(f"{API}/iot/thingspeak", headers=operador_headers)
    assert res.status_code == 502 and "recusou o acesso" in res.json()["detail"]


def test_route_when_not_configured(client, operador_headers, thingspeak_override):
    thingspeak_override(ok, thingspeak_channel_id="")
    body = client.get(f"{API}/iot/thingspeak", headers=operador_headers).json()
    assert body["configured"] is False and body["channel"] is None


# ------------------------------------------------------------------ localização do dispositivo
def build(channel: dict, feeds: list[dict]):
    return build_feed({"channel": channel, "feeds": feeds}, channel_id="4242", period="recent", aggregation="", units={}, refresh_seconds=60, fetched_at=datetime.now(timezone.utc))


def test_position_from_entry_location_parameters():
    feeds = [
        {"created_at": "2026-10-14T10:00:00Z", "entry_id": 1, "field1": "30", "latitude": "-9.40", "longitude": "-40.51"},
        {"created_at": "2026-10-14T10:01:00Z", "entry_id": 2, "field1": "31", "latitude": "0.0", "longitude": "0.0"},  # 0,0 = sem posição
        {"created_at": "2026-10-14T10:02:00Z", "entry_id": 3, "field1": "32", "latitude": None, "longitude": None},
    ]
    feed = build(CHANNEL, feeds)
    assert feed.position.source == "registro" and (feed.position.latitude, feed.position.longitude) == (-9.40, -40.51)
    assert feed.position.at == datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    assert [e.latitude for e in feed.entries] == [-9.40, None, None] and feed.location_fields == []


def test_position_from_latitude_longitude_fields_is_kept_out_of_charts():
    channel = {**CHANNEL, "field3": "Latitude", "field4": "Longitude"}
    feeds = [{"created_at": "2026-10-14T10:00:00Z", "entry_id": 1, "field1": "30", "field3": "-9,38", "field4": "-40.49"}]
    feed = build(channel, feeds)
    assert [f.key for f in feed.fields] == ["field1", "field2"] and feed.location_fields == ["field3", "field4"]
    assert feed.position.source == "campos" and feed.position.latitude == -9.38 and "field3" not in feed.entries[0].values


def test_position_falls_back_to_channel_and_ignores_zero():
    assert build(CHANNEL, FEEDS).position.source == "canal"
    assert build({**CHANNEL, "latitude": "0.0", "longitude": "0.0"}, FEEDS).position is None


def test_location_is_requested():
    svc, calls = make_service(ok)
    svc.feed()
    assert calls[0].url.params["location"] == "true"
