"""
Integração com a plataforma AgroAPI da Embrapa: RespondeAgro v1 e Agritec v2.

Documentação oficial consultada (10/2026):
- Acesso e OAuth2: https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/agroapi-primeiros-passos.pdf
- RespondeAgro: https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/respondeagro.pdf
  (Swagger: https://www.agroapi.cnptia.embrapa.br/store/api-docs/agroapi/RespondeAgro/v1)
- Agritec v2: https://www.agroapi.cnptia.embrapa.br/portal/assets/docs/agritec.pdf
  (OpenAPI: https://www.agroapi.cnptia.embrapa.br/store/api-docs/agroapi/Agritec/v2)

Autenticação: POST {EMBRAPA_API_URL}/token com grant_type=client_credentials e Basic
(consumer key:consumer secret). O token vale 3.600 s por padrão e é renovado automaticamente.

Recursos usados:
- RespondeAgro: POST /respondeagro/v1/_search/template (templates query_one_book, query_all e book_ids)
  e GET /respondeagro/v1/_doc/{id}. As respostas vêm em HTML e são sanitizadas (services/sanitize.py).
- Agritec: GET /agritec/v2/culturas, /agritec/v2/zoneamento, /agritec/v2/culturas/{id}/informacoes-adicionais
  e /agritec/v2/solos.

Planos (documentação oficial): RespondeAgro gratuito até 100 mil requisições/mês; Agritec gratuito
por um mês e até 1.000 requisições. Por isso tudo fica em cache (24 h no RespondeAgro e 7 dias no
Agritec, configuráveis) e, se a AgroAPI falhar, a última resposta obtida (até 7 dias) é reaproveitada.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from html import escape
from typing import Any, Literal, TypeVar

import httpx

from ..catalog import ANOMALY_EMBRAPA_QUERY, ANOMALY_TEXT, MUNICIPALITY_BY_IBGE, SEVERE_ANOMALIES
from ..config import Settings, get_settings
from ..schemas import (
    AgritecCultureOut,
    AgritecWindowOut,
    AgritecZarcOut,
    AnomalyGuidanceOut,
    EmbrapaBookOut,
    EmbrapaQuestionOut,
    EmbrapaSearchOut,
    IntegrationState,
    ReadingGuidanceOut,
)
from .external import USER_AGENT, ExternalServiceError, KeyedLocks, TTLCache
from .sanitize import excerpt, sanitize_html

log = logging.getLogger("oasis.embrapa")

T = TypeVar("T")
Scope = Literal["grape", "all"]

SOURCE_RESPONDE = "Embrapa — Coleção 500 Perguntas 500 Respostas, via API RespondeAgro (AgroAPI)"
SOURCE_AGRITEC = "Embrapa/MAPA — Zoneamento Agrícola de Risco Climático (Zarc), via API Agritec v2 (AgroAPI)"
STALE_SECONDS = 7 * 24 * 3600
GRAPE = re.compile(r"\bUVAS?\b")
DOC_ID = re.compile(r"^[a-z0-9-]{1,40}_\d{1,4}$")
_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)


# ------------------------------------------------------------------ conversões
def _plain(raw: Any) -> str | None:
    """Texto puro (remove marcação e normaliza espaços)."""
    if raw is None:
        return None
    text = sanitize_html(str(raw))[1]
    return text or None


def _int(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _url(raw: Any) -> str | None:
    text = str(raw).strip() if raw else ""
    return text if _URL.match(text) else None


def fold(text: str) -> str:
    """Sem acentos e em maiúsculas (comparação de nomes de culturas)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).upper()


def parse_question(doc: Any) -> EmbrapaQuestionOut | None:
    """Documento do índice responde_agro (`_id`, `_source`, `highlight`) → pergunta pronta para exibir."""
    if not isinstance(doc, dict) or not isinstance(doc.get("_source"), dict):
        return None
    src = doc["_source"]
    question = _plain(src.get("question"))
    if not question:
        return None
    answer_html, answer_text = sanitize_html(src.get("answer"))
    highlight = doc.get("highlight") if isinstance(doc.get("highlight"), dict) else {}
    hq = [h for h in highlight.get("question") or [] if isinstance(h, str)]
    ha = [h for h in highlight.get("answer") or [] if isinstance(h, str)]
    short = excerpt(answer_text)
    return EmbrapaQuestionOut(
        id=str(doc.get("_id") or ""),
        number=_int(src.get("question_number")),
        question=question,
        question_html=sanitize_html(hq[0])[0] if hq else escape(question),
        answer_html=answer_html,
        answer_excerpt=short,
        snippet_html=" … ".join(sanitize_html(f)[0] for f in ha[:2]) if ha else escape(short),
        chapter=_plain(src.get("chapter")),
        book=_plain(src.get("book")),
        book_id=_plain(src.get("book_id")),
        year=_int(src.get("year")),
        pdf_url=_url(src.get("pdf")),
        epub_url=_url(src.get("epub")),
    )


def parse_search(data: Any, *, query: str, scope: str, book_id: str | None, page: int, size: int, fetched_at: datetime) -> EmbrapaSearchOut:
    if not isinstance(data, dict) or not isinstance(data.get("hits"), dict):
        raise ExternalServiceError("O RespondeAgro devolveu uma resposta em formato inesperado.", 502)
    hits = data["hits"]
    items = [q for h in hits.get("hits") or [] if (q := parse_question(h))]
    raw_total = hits.get("total")
    if isinstance(raw_total, dict):
        total, exact = _int(raw_total.get("value")) or 0, raw_total.get("relation", "eq") == "eq"
    elif isinstance(raw_total, int):
        total, exact = raw_total, True
    else:
        total, exact = (page - 1) * size + len(items), False
    return EmbrapaSearchOut(
        query=query, scope=scope, book_id=book_id, total=total, total_is_exact=exact, page=page, page_size=size,
        items=items, fetched_at=fetched_at, source=SOURCE_RESPONDE,
    )


def parse_culture(raw: Any) -> AgritecCultureOut | None:
    if not isinstance(raw, dict):
        return None
    cid, name = _int(raw.get("id")), _plain(raw.get("nome") or raw.get("cultura"))
    if cid is None or not name:
        return None
    return AgritecCultureOut(
        id=cid, name=name, full_name=_plain(raw.get("nomeCompleto")), harvest=_plain(raw.get("safra")),
        cultivation=_plain(raw.get("cultivo")), climate=_plain(raw.get("clima")), has_zoning=bool(raw.get("hasZoneamento")),
    )


def parse_window(raw: Any) -> AgritecWindowOut | None:
    """Janela do Zarc; descarta registros sem datas válidas (dia 1–31, mês 1–12)."""
    if not isinstance(raw, dict):
        return None
    d0, m0, d1, m1 = (_int(raw.get(k)) for k in ("diaIni", "mesIni", "diaFim", "mesFim"))
    if None in (d0, m0, d1, m1) or not (1 <= m0 <= 12 and 1 <= m1 <= 12 and 1 <= d0 <= 31 and 1 <= d1 <= 31):  # type: ignore[operator]
        return None
    return AgritecWindowOut(
        cycle=_plain(raw.get("ciclo")), soil=_plain(raw.get("solo")), start_day=d0, start_month=m0, end_day=d1, end_month=m1,  # type: ignore[arg-type]
        risk=_int(raw.get("risco")), harvest_start=_int(raw.get("safraIni")), harvest_end=_int(raw.get("safraFim")), ordinance=_plain(raw.get("portaria")),
    )


def _data(payload: Any, what: str) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        raise ExternalServiceError(f"O Agritec devolveu uma resposta em formato inesperado ({what}).", 502)
    return payload["data"]


def _agritec_error(res: httpx.Response) -> str | None:
    """Mensagem do corpo de erro documentado do Agritec: {"error": {"summary", "fieldErrors": [...]}}."""
    try:
        err = res.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    fields = [f.get("summary") for f in err.get("fieldErrors") or [] if isinstance(f, dict) and f.get("summary")]
    return _plain(fields[0] if fields else err.get("summary"))


GUIDANCE_ITEMS = 3


def strongest_anomalies(detections: list[tuple[str, float]], min_confidence: float) -> list[tuple[str, float]]:
    """Uma entrada por classe de anomalia (maior confiança), graves primeiro; ignora classes sem consulta."""
    best: dict[str, float] = {}
    for label, conf in detections:
        if label in ANOMALY_EMBRAPA_QUERY and conf >= min_confidence and conf > best.get(label, -1):
            best[label] = conf
    return sorted(best.items(), key=lambda lc: (lc[0] not in SEVERE_ANOMALIES, -lc[1]))


# ------------------------------------------------------------------ serviço
class EmbrapaService:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport  # testes injetam httpx.MockTransport
        self.cache = TTLCache(max_items=512)
        self.locks = KeyedLocks()
        self._token: tuple[str, float] | None = None
        self._token_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.settings.embrapa_configured

    @property
    def base(self) -> str:
        return self.settings.embrapa_api_url.rstrip("/")

    def status(self) -> IntegrationState:
        if not self.configured:
            return IntegrationState(configured=False, detail="Credenciais da AgroAPI não configuradas (EMBRAPA_CONSUMER_KEY e EMBRAPA_CONSUMER_SECRET).")
        return IntegrationState(configured=True, detail="Credenciais da AgroAPI configuradas (RespondeAgro v1 e Agritec v2).")

    def _require(self) -> None:
        if not self.configured:
            raise ExternalServiceError(
                "A integração com a Embrapa ainda não foi configurada no servidor. Defina EMBRAPA_CONSUMER_KEY e EMBRAPA_CONSUMER_SECRET (aplicação criada na loja da AgroAPI).",
                503,
            )

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.settings.embrapa_timeout_seconds, transport=self.transport, headers={"User-Agent": USER_AGENT})

    def _access_token(self, client: httpx.Client, renew: bool = False) -> str:
        with self._token_lock:
            now = time.monotonic()
            if not renew and self._token and now < self._token[1]:
                return self._token[0]
            s = self.settings
            try:
                res = client.post(f"{self.base}/token", data={"grant_type": "client_credentials"}, auth=(s.embrapa_consumer_key, s.embrapa_consumer_secret))
            except httpx.TimeoutException:
                raise ExternalServiceError("A AgroAPI da Embrapa demorou demais para gerar o token de acesso. Tente novamente.", 504)
            except httpx.HTTPError:
                raise ExternalServiceError("Não foi possível conectar à AgroAPI da Embrapa.", 503)
            if res.status_code in (400, 401):
                raise ExternalServiceError("A AgroAPI recusou as credenciais da aplicação. Confira EMBRAPA_CONSUMER_KEY e EMBRAPA_CONSUMER_SECRET.", 502)
            if res.status_code >= 400:
                raise ExternalServiceError(f"A AgroAPI respondeu com erro {res.status_code} ao gerar o token de acesso.", 503 if res.status_code >= 500 else 502)
            try:
                body = res.json()
                token, expires = str(body["access_token"]), float(body.get("expires_in") or 3600)
            except (ValueError, KeyError, TypeError):
                raise ExternalServiceError("A AgroAPI devolveu uma resposta inválida ao gerar o token de acesso.", 502)
            # Validade "indeterminada" (-1 na loja) chega como número enorme: renova ao menos a cada 24 h, com 60 s de folga.
            ttl = min(expires, 86400) - 60 if expires > 0 else 86400
            self._token = (token, now + max(ttl, 30))
            return token

    def _call(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None, not_found_ok: bool = False) -> Any:
        with self._client() as client:
            for attempt in (0, 1):
                token = self._access_token(client, renew=attempt == 1)
                try:
                    res = client.request(method, f"{self.base}{path}", params=params, json=json, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
                except httpx.TimeoutException:
                    raise ExternalServiceError("A AgroAPI da Embrapa demorou demais para responder. Tente novamente em instantes.", 504)
                except httpx.HTTPError:
                    raise ExternalServiceError("Não foi possível conectar à AgroAPI da Embrapa.", 503)
                if res.status_code != 401:
                    break  # 401 com token em cache = token expirado ou revogado: renova uma vez
        if res.status_code == 404 and not_found_ok:
            return None
        if res.status_code in (401, 403):
            raise ExternalServiceError(
                "A AgroAPI negou o acesso. Verifique se a aplicação está inscrita (Subscribe) nas APIs RespondeAgro v1 e Agritec v2 e se o plano está ativo.", 502
            )
        if res.status_code == 429:
            raise ExternalServiceError("O limite de requisições do plano da AgroAPI foi atingido. As consultas voltam a funcionar quando a cota for renovada.", 503)
        if res.status_code >= 500:
            raise ExternalServiceError(f"A AgroAPI está indisponível no momento (erro {res.status_code}). Tente novamente mais tarde.", 503)
        if res.status_code >= 400:
            detail = _agritec_error(res)
            raise ExternalServiceError(f"A AgroAPI recusou a consulta: {detail}" if detail else f"A AgroAPI recusou a consulta (erro {res.status_code}).", 502)
        try:
            return res.json()
        except ValueError:
            raise ExternalServiceError("A AgroAPI devolveu uma resposta que não é JSON válido.", 502)

    def _cached(self, key: str, hours: float, loader: Callable[[], T]) -> T:
        hit = self.cache.get(key)
        if hit:
            return hit.value
        with self.locks(key):
            hit = self.cache.get(key)
            if hit:
                return hit.value
            try:
                value = loader()
            except ExternalServiceError as exc:
                previous = self.cache.get(key, stale_for=STALE_SECONDS)
                if previous and exc.status_code != 404:
                    log.warning("AgroAPI indisponível (%s); reaproveitando a última resposta obtida.", exc.message)
                    return previous.value
                raise
            self.cache.set(key, value, hours * 3600)
            return value

    # ---------------------------------------------------------------- RespondeAgro
    def search(self, query: str, page: int = 1, size: int = 8, scope: Scope = "grape") -> EmbrapaSearchOut:
        self._require()
        q = " ".join(query.split())[:120]
        book = self.settings.embrapa_book_id if scope == "grape" else None
        start = (page - 1) * size
        params: dict[str, Any] = {"query_string": q, "from": start, "size": size}
        if book:
            params["book_id"] = book
        template = {"id": "query_one_book" if book else "query_all", "params": params}

        def load() -> EmbrapaSearchOut:
            data = self._call("POST", "/respondeagro/v1/_search/template", json=template)
            return parse_search(data, query=q, scope=scope, book_id=book, page=page, size=size, fetched_at=datetime.now(timezone.utc))

        return self._cached(f"ra:search:{book or '*'}:{q.lower()}:{start}:{size}", self.settings.embrapa_cache_hours, load)

    def question(self, doc_id: str) -> EmbrapaQuestionOut | None:
        self._require()
        if not DOC_ID.match(doc_id):
            return None

        def load() -> EmbrapaQuestionOut | None:
            data = self._call("GET", f"/respondeagro/v1/_doc/{doc_id}", not_found_ok=True)
            if not isinstance(data, dict) or data.get("found") is False:
                return None
            return parse_question(data)

        return self._cached(f"ra:doc:{doc_id}", self.settings.embrapa_cache_hours, load)

    def books(self) -> list[EmbrapaBookOut]:
        self._require()

        def load() -> list[EmbrapaBookOut]:
            data = self._call("POST", "/respondeagro/v1/_search/template", json={"id": "book_ids"})
            buckets = (((data or {}).get("aggregations") or {}).get("book_ids") or {}).get("buckets") if isinstance(data, dict) else None
            if not isinstance(buckets, list):
                raise ExternalServiceError("O RespondeAgro devolveu uma resposta em formato inesperado (livros).", 502)
            return [EmbrapaBookOut(id=str(b["key"]), questions=_int(b.get("doc_count")) or 0) for b in buckets if isinstance(b, dict) and b.get("key")]

        return self._cached("ra:books", 24 * 7, load)

    def guidance(self, reading_id: str, model_version: str, anomalies: list[tuple[str, float]]) -> ReadingGuidanceOut:
        """
        Cruza as anomalias detectadas pelo modelo (YOLO ou análise de cor) com o livro de uva do
        RespondeAgro. Sem anomalias, nenhuma consulta é feita; sem credenciais, nada é inventado.
        """
        base = {"reading_id": reading_id, "model_version": model_version, "source": SOURCE_RESPONDE}
        if not anomalies:
            return ReadingGuidanceOut(**base, configured=self.configured, anomalies=[], detail="Nenhuma anomalia detectada nesta leitura.")
        if not self.configured:
            return ReadingGuidanceOut(**base, configured=False, anomalies=[], detail=self.status().detail)
        out, fetched = [], None
        for label, conf in anomalies:
            query = ANOMALY_EMBRAPA_QUERY[label]
            found = self.search(query, 1, GUIDANCE_ITEMS, "grape")
            fetched = max(fetched, found.fetched_at) if fetched else found.fetched_at
            out.append(AnomalyGuidanceOut(
                anomaly=label, label=ANOMALY_TEXT[label][0], confidence=round(conf, 3), severe=label in SEVERE_ANOMALIES,
                query=query, total=found.total, items=found.items,
            ))
        return ReadingGuidanceOut(**base, configured=True, anomalies=out, fetched_at=fetched)

    # ---------------------------------------------------------------- Agritec
    def grape_cultures(self) -> list[AgritecCultureOut]:
        """Culturas de uva presentes no Agritec (filtradas pelo nome; nenhum identificador é fixado no código)."""
        self._require()

        def load() -> list[AgritecCultureOut]:
            items = _data(self._call("GET", "/agritec/v2/culturas"), "culturas")
            if not isinstance(items, list):
                raise ExternalServiceError("O Agritec devolveu uma resposta em formato inesperado (culturas).", 502)
            cultures = [c for c in map(parse_culture, items) if c and GRAPE.search(fold(c.name))]
            return sorted(cultures, key=lambda c: c.name)

        return self._cached("ag:cultures", self.settings.embrapa_agritec_cache_hours, load)

    def zarc(self, culture_id: int, ibge: int) -> AgritecZarcOut:
        self._require()
        municipality = MUNICIPALITY_BY_IBGE.get(ibge)
        if municipality is None:
            raise ExternalServiceError("Município fora da lista disponível para consulta.", 422)
        culture = next((c for c in self.grape_cultures() if c.id == culture_id), None)
        if culture is None:
            raise ExternalServiceError("Cultura não encontrada entre as culturas de uva do Agritec.", 404)
        hours = self.settings.embrapa_agritec_cache_hours

        def load_windows() -> tuple[list[AgritecWindowOut], str | None, datetime]:
            items = _data(self._call("GET", "/agritec/v2/zoneamento", params={"idCultura": culture_id, "codigoIBGE": ibge, "risco": "todos"}), "zoneamento")
            raw = items if isinstance(items, list) else []
            windows = sorted(
                (w for w in map(parse_window, raw) if w),
                key=lambda w: (w.cycle or "", w.soil or "", w.risk or 0, w.start_month, w.start_day),
            )
            uf = next((_plain(r.get("uf")) for r in raw if isinstance(r, dict) and r.get("uf")), None)
            return windows, uf, datetime.now(timezone.utc)

        def load_info() -> dict[str, str | None]:
            data = self._call("GET", f"/agritec/v2/culturas/{culture_id}/informacoes-adicionais", not_found_ok=True)
            info = data.get("data") if isinstance(data, dict) else None
            if not isinstance(info, dict):
                return {}
            return {k: _plain(info.get(k)) for k in ("safra", "portaria", "grupos", "solos")}

        def load_soils() -> dict[str, str]:
            items = _data(self._call("GET", "/agritec/v2/solos"), "solos")
            return {str(s["tipo"]): _plain(s.get("descricao")) or "" for s in items or [] if isinstance(s, dict) and s.get("tipo")}

        windows, uf, fetched_at = self._cached(f"ag:zarc:{culture_id}:{ibge}", hours, load_windows)
        info = self._cached(f"ag:info:{culture_id}", hours, load_info)
        soils = self._cached("ag:soils", 24 * 30, load_soils)
        used = {w.soil for w in windows if w.soil}
        return AgritecZarcOut(
            culture=culture, municipality=municipality["name"], ibge_code=ibge, uf=uf or municipality["uf"], windows=windows,
            soils={k: v for k, v in soils.items() if k in used}, info=info, fetched_at=fetched_at, source=SOURCE_AGRITEC,
        )


@lru_cache
def get_embrapa() -> EmbrapaService:
    return EmbrapaService(get_settings())
