"""
Leitura de canais do ThingSpeak pela API REST (https://www.mathworks.com/help/thingspeak/readdata.html).

- Canal público: basta THINGSPEAK_CHANNEL_ID. Canal privado: também THINGSPEAK_READ_API_KEY
  (aba "API Keys" do canal). As credenciais MQTT de um dispositivo (client ID, usuário e senha)
  servem para publicar e assinar tópicos no broker MQTT do ThingSpeak e não são aceitas pela
  API REST de leitura; por isso a API OASIS não as utiliza.
- Os nomes dos campos (field1 … field8) vêm do próprio canal. Nenhuma grandeza é presumida e
  a unidade só aparece quando informada em THINGSPEAK_FIELD_UNITS.
- As respostas ficam em cache por THINGSPEAK_CACHE_SECONDS (mín. 15 s, o intervalo mínimo de
  atualização de um canal no plano gratuito). Se o ThingSpeak falhar, a última resposta válida
  (até 1 h) é devolvida marcada como desatualizada.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal

import httpx

from ..config import Settings, get_settings
from ..schemas import IntegrationState, ThingSpeakChannelOut, ThingSpeakEntryOut, ThingSpeakFeedOut, ThingSpeakFieldOut
from .external import USER_AGENT, ExternalServiceError, KeyedLocks, TTLCache

log = logging.getLogger("oasis.thingspeak")

Period = Literal["recent", "24h", "7d", "30d"]
FIELD_KEYS = tuple(f"field{i}" for i in range(1, 9))
STALE_SECONDS = 3600

# Períodos longos usam a amostragem do próprio ThingSpeak (`timescale`: primeiro registro de cada
# janela), que devolve leituras reais; `average` trata NaN como 0 e poderia distorcer os valores.
PERIODS: dict[str, tuple[dict[str, int], str]] = {
    "recent": ({}, ""),
    "24h": ({"days": 1, "timescale": 10}, "Últimas 24 h · primeiro registro de cada janela de 10 min"),
    "7d": ({"days": 7, "timescale": 60}, "Últimos 7 dias · primeiro registro de cada hora"),
    "30d": ({"days": 30, "timescale": 240}, "Últimos 30 dias · primeiro registro de cada janela de 4 h"),
}

_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


def parse_number(raw: Any) -> float | None:
    """Valor de um campo → número finito. Vazio, texto, NaN ou infinito → None (aceita vírgula decimal)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(raw) else None
    text = str(raw).strip()
    if not _NUMBER.match(text):
        return None
    value = float(text.replace(",", "."))
    return value if math.isfinite(value) else None


def parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_units(spec: str) -> dict[str, str]:
    """"field1=°C,field2=%" → {"field1": "°C", "field2": "%"} (chaves inválidas são ignoradas)."""
    units: dict[str, str] = {}
    for part in re.split(r"[;,]", spec or ""):
        key, sep, unit = part.partition("=")
        if sep and key.strip() in FIELD_KEYS and unit.strip():
            units[key.strip()] = unit.strip()[:16]
    return units


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _int(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


def build_feed(payload: Any, *, channel_id: str, period: str, aggregation: str, units: dict[str, str], refresh_seconds: int, fetched_at: datetime) -> ThingSpeakFeedOut:
    """Valida e converte a resposta de /channels/{id}/feeds.json."""
    if not isinstance(payload, dict) or not isinstance(payload.get("channel"), dict) or not isinstance(payload.get("feeds"), list):
        raise ExternalServiceError("O ThingSpeak devolveu uma resposta em formato inesperado.", 502)
    ch = payload["channel"]
    channel = ThingSpeakChannelOut(
        id=_int(ch.get("id")) or int(channel_id),
        name=_text(ch.get("name")),
        description=_text(ch.get("description")),
        latitude=parse_number(ch.get("latitude")),
        longitude=parse_number(ch.get("longitude")),
        created_at=parse_time(ch.get("created_at")),
        updated_at=parse_time(ch.get("updated_at")),
        last_entry_id=_int(ch.get("last_entry_id")),
    )
    # Campos configurados no canal = os que têm nome. O nome é a única descrição do significado do campo.
    names = {k: _text(ch.get(k)) for k in FIELD_KEYS}
    keys = [k for k in FIELD_KEYS if names[k]]

    rows = [(at, feed) for feed in payload["feeds"] if isinstance(feed, dict) and (at := parse_time(feed.get("created_at")))]
    rows.sort(key=lambda r: r[0])
    entries = [ThingSpeakEntryOut(created_at=at, entry_id=_int(feed.get("entry_id")), values={k: parse_number(feed.get(k)) for k in keys}) for at, feed in rows]

    fields: list[ThingSpeakFieldOut] = []
    for key in keys:
        numbers = [(e.created_at, e.values[key]) for e in entries if e.values[key] is not None]
        texts = [(at, str(feed[key]).strip()) for at, feed in rows if _text(feed.get(key)) and parse_number(feed.get(key)) is None]
        numeric = len(numbers) >= len(texts)
        values = [v for _, v in numbers]
        if numeric:
            latest_at, latest = numbers[-1] if numbers else (None, None)
            latest_text = None
        else:
            latest_at, latest_text = texts[-1]
            latest, latest_text = None, latest_text[:120]
        fields.append(
            ThingSpeakFieldOut(
                key=key,
                name=names[key] or key,
                unit=units.get(key),
                numeric=numeric,
                latest=latest,
                latest_text=latest_text,
                latest_at=latest_at,
                count=len(values) if numeric else len(texts),
                min=min(values) if numeric and values else None,
                max=max(values) if numeric and values else None,
                avg=round(sum(values) / len(values), 4) if numeric and values else None,
            )
        )

    return ThingSpeakFeedOut(
        configured=True,
        channel_id=channel_id,
        period=period,
        aggregation=aggregation,
        channel=channel,
        fields=fields,
        entries=entries,
        last_entry_at=entries[-1].created_at if entries else None,
        fetched_at=fetched_at,
        refresh_seconds=refresh_seconds,
    )


class ThingSpeakService:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport  # testes injetam httpx.MockTransport
        self.units = parse_units(self.settings.thingspeak_field_units)
        self.cache = TTLCache(max_items=32)
        self.locks = KeyedLocks()

    @property
    def configured(self) -> bool:
        return self.settings.thingspeak_configured

    def status(self) -> IntegrationState:
        if not self.configured:
            return IntegrationState(configured=False, detail="Canal não configurado (THINGSPEAK_CHANNEL_ID).")
        kind = "privado, com Read API Key" if self.settings.thingspeak_read_api_key else "público"
        return IntegrationState(configured=True, detail=f"Canal configurado ({kind}).")

    def feed(self, period: Period = "recent", results: int = 100) -> ThingSpeakFeedOut:
        s = self.settings
        if not self.configured:
            return ThingSpeakFeedOut(
                configured=False, channel_id=None, period=period, aggregation=None, channel=None, fields=[], entries=[],
                last_entry_at=None, fetched_at=None, refresh_seconds=s.thingspeak_refresh_seconds,
            )
        params, aggregation = PERIODS[period]
        query: dict[str, int] = dict(params)
        if period == "recent":
            # `results` tem precedência sobre as agregações no ThingSpeak: só é usado no período sem agregação.
            query["results"] = results
            aggregation = f"Últimos {results} registros, sem agregação"
        key = f"{s.thingspeak_channel_id}:{period}:{sorted(query.items())}"
        hit = self.cache.get(key)
        if hit:
            return hit.value
        with self.locks(key):
            hit = self.cache.get(key)
            if hit:
                return hit.value
            try:
                feed = build_feed(
                    self._fetch(query), channel_id=s.thingspeak_channel_id, period=period, aggregation=aggregation,
                    units=self.units, refresh_seconds=s.thingspeak_refresh_seconds, fetched_at=datetime.now(timezone.utc),
                )
            except ExternalServiceError as exc:
                previous = self.cache.get(key, stale_for=STALE_SECONDS)
                if previous:
                    log.warning("ThingSpeak indisponível (%s); devolvendo a última resposta válida.", exc.message)
                    return previous.value.model_copy(update={"stale": True, "warning": exc.message})
                raise
            self.cache.set(key, feed, s.thingspeak_cache_seconds)
            return feed

    def _fetch(self, query: dict[str, int]) -> Any:
        s = self.settings
        params: dict[str, Any] = dict(query)
        if s.thingspeak_read_api_key:
            params["api_key"] = s.thingspeak_read_api_key  # a API de leitura só aceita a chave na URL; httpx não registra URLs (main.py)
        url = f"{s.thingspeak_api_url.rstrip('/')}/channels/{s.thingspeak_channel_id}/feeds.json"
        try:
            with httpx.Client(timeout=s.thingspeak_timeout_seconds, transport=self.transport, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
                res = client.get(url, params=params)
        except httpx.TimeoutException:
            raise ExternalServiceError("O ThingSpeak demorou demais para responder. Tente novamente em instantes.", 504)
        except httpx.HTTPError:
            raise ExternalServiceError("Não foi possível conectar ao ThingSpeak. Verifique a conexão do servidor com a internet.", 503)

        body = res.text.strip()
        if res.status_code in (400, 401, 403) or body == "-1":
            # A documentação do ThingSpeak responde "-1" quando não há acesso ao canal.
            raise ExternalServiceError("O ThingSpeak recusou o acesso ao canal. Se ele for privado, confira a THINGSPEAK_READ_API_KEY (aba API Keys do canal).", 502)
        if res.status_code == 404:
            raise ExternalServiceError(f"Canal {s.thingspeak_channel_id} não encontrado no ThingSpeak. Confira o THINGSPEAK_CHANNEL_ID.", 502)
        if res.status_code == 429:
            raise ExternalServiceError("O ThingSpeak limitou temporariamente as consultas (429). Aguarde alguns instantes.", 503)
        if res.status_code >= 400:
            raise ExternalServiceError(f"O ThingSpeak respondeu com erro {res.status_code}. Tente novamente mais tarde.", 503 if res.status_code >= 500 else 502)
        try:
            return res.json()
        except ValueError:
            raise ExternalServiceError("O ThingSpeak devolveu uma resposta que não é JSON válido.", 502)


@lru_cache
def get_thingspeak() -> ThingSpeakService:
    return ThingSpeakService(get_settings())
