"""
Integração com a Embrapa AgroAPI (RespondeAgro v1 e Agritec v2).
As respostas simuladas seguem os exemplos da documentação oficial (respondeagro.pdf e OpenAPI do Agritec v2).
"""

import base64
import json

import httpx
import pytest

from app.config import Settings
from app.main import app
from app.routers.integrations import reset_embrapa_rate_limit
from app.services import external
from app.services.embrapa import EmbrapaService, get_embrapa, parse_question
from app.services.external import ExternalServiceError
from app.services.sanitize import sanitize_html

from .conftest import API

DOC_ILPF = {  # GET /respondeagro/v1/_doc/ilpf_400 (respondeagro.pdf, Figura 1)
    "_index": "responde_agro", "_id": "ilpf_400", "_version": 1, "found": True,
    "_source": {
        "question_number": 400,
        "question": "A recuperação de uma pastagem degradada por meio do uso cultivos anuais possibilita a substituição da mesma por outra espécie ou cultivar?",
        "answer": "<p>Sim, a utilização de cultivos anuais na recuperação de pastagens degradadas facilita a substituição de cultivares forrageiras.</p>",
        "chapter": "Potencial para Adoção da Estratégia de Integração Lavoura-Pecuária", "book": "Integração Lavoura-Pecuária-Floresta",
        "book_id": "ilpf", "epub": "https://mais500p500r.sct.embrapa.br/view/epubs/90000033/90000033-ebook-epub.epub",
        "pdf": "https://mais500p500r.sct.embrapa.br/view/pdfs/90000033-ebook-pdf.pdf", "year": 2015,
    },
}


def hit(n: int, question: str, answer: str, highlight: dict | None = None) -> dict:
    doc = {"_index": "responde_agro", "_id": f"uva_{n}", "_score": 10.0 - n, "_source": {
        "question_number": n, "question": question, "answer": answer, "chapter": "Poda", "book": "Uva", "book_id": "uva", "year": 2020,
        "pdf": "https://exemplo.embrapa.br/uva.pdf", "epub": "javascript:alert(1)",
    }}
    if highlight:
        doc["highlight"] = highlight
    return doc


SEARCH = {  # POST /_search/template (formato da Figura 3)
    "took": 3, "timed_out": False,
    "hits": {"total": {"value": 24, "relation": "eq"}, "max_score": 9.0, "hits": [
        hit(1, "Quando fazer a poda?", "<p>A poda é feita <script>alert(1)</script>após a colheita.</p>",
            {"question": ["Quando fazer a <span class='highlight'>poda</span>?"], "answer": ["<p>A <span class='highlight'>poda</span> é feita</p>"]}),
        hit(2, "O que é poda verde?", "<p onclick='x()'>Retirada de ramos <img src='data:image/png;base64,iVBORw0KGgo=' alt='Figura 1'><img src='http://rastreador/x.png'></p>"),
        {"_id": "sem-fonte"},
    ]},
}

CULTURES = {"meta": {"totalCount": 5}, "data": [
    {"id": 101, "nome": "ABACAXI IRRIGADO", "hasZoneamento": True},
    {"id": 7001, "nome": "UVA EUROPEIA IRRIGADA", "nomeCompleto": "UVA EUROPEIA IRRIGADA", "safra": "Perene", "cultivo": "IRRIGADO", "clima": "Não se aplica", "hasZoneamento": True},
    {"id": 7002, "nome": "UVA AMERICANA E HÍBRIDA", "hasZoneamento": True},
    {"id": 7003, "nome": "CHUVAS DE VERÃO"},  # contém "UVA" no meio da palavra: não é uva
    {"id": None, "nome": "UVA SEM ID"},
]}

ZARC = {"meta": {"totalCount": 3}, "data": [
    {"municipio": "PETROLINA", "uf": "PE", "cultura": "UVA EUROPEIA IRRIGADA", "ciclo": "PERENE", "solo": "AD2", "diaIni": 1, "mesIni": 4, "diaFim": 31, "mesFim": 5, "safraIni": 2026, "safraFim": 2027, "risco": 30, "portaria": "Port. 1 de 01/01/2026"},
    {"municipio": "PETROLINA", "uf": "PE", "cultura": "UVA EUROPEIA IRRIGADA", "ciclo": "PERENE", "solo": "AD1", "diaIni": 11, "mesIni": 3, "diaFim": 30, "mesFim": 6, "safraIni": 2026, "safraFim": 2027, "risco": 20, "portaria": "Port. 1 de 01/01/2026"},
    {"municipio": "PETROLINA", "uf": "PE", "ciclo": "PERENE", "solo": "AD3", "diaIni": 40, "mesIni": 13, "diaFim": 1, "mesFim": 1, "risco": 20},
]}
SOLOS = {"meta": {"totalCount": 3}, "data": [{"tipo": "AD1", "descricao": "AD1: 0.34 até 0.46 (mm/cm)"}, {"tipo": "AD2", "descricao": "AD2: 0.46 até 0.61 (mm/cm)"}, {"tipo": "ARGILOSO", "descricao": "Argiloso"}]}
INFO = {"data": {"safra": "2026-2027", "portaria": "Port. 412 de 30/12/2020", "grupos": "Perene, Semiperene, I a VI.", "solos": "Capacidade de água no solo."}}


class FakeAgroAPI:
    """Simula o gateway da AgroAPI: /token (OAuth2) e os recursos do RespondeAgro e do Agritec."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.tokens = 0
        self.reject_token_once = False
        self.routes: dict[tuple[str, str], httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/token":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.tokens}", "token_type": "Bearer", "expires_in": 3600})
        if self.reject_token_once and request.headers.get("authorization") == "Bearer tok-1":
            return httpx.Response(401, text="<ams:fault><ams:code>900901</ams:code><ams:message>Invalid Credentials</ams:message></ams:fault>")
        response = self.routes.get((request.method, request.url.path))
        return response if response is not None else httpx.Response(404, text="<am:fault>No matching resource found for given API Request</am:fault>")

    def api_calls(self, path: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.path == path]


def make_service(api, **overrides) -> EmbrapaService:
    settings = Settings(_env_file=None, **{"embrapa_consumer_key": "chave-app", "embrapa_consumer_secret": "segredo-app", **overrides})
    return EmbrapaService(settings, transport=httpx.MockTransport(api))


# ------------------------------------------------------------------ sanitização e conversão
def test_sanitizer_keeps_text_markup_and_removes_active_content():
    html, text = sanitize_html(
        "<p onclick='x()'>Olá <b>mundo</b><script>alert(1)</script><style>p{}</style>"
        "<a href='javascript:alert(1)'>link</a> <a href='https://embrapa.br'>Embrapa</a>"
        "<img src='data:image/png;base64,iVBORw0KGgo=' onerror='x()'><img src='https://externo/x.png'>"
        "<span class='highlight' style='color:red'>poda</span><iframe><p>oculto</p></iframe> &lt;tag&gt;<ul><li>sem fechar"
    )
    assert "script" not in html and "alert" not in html and "onclick" not in html and "onerror" not in html and "style" not in html
    assert "oculto" not in html and "https://externo" not in html
    assert '<a href="https://embrapa.br" target="_blank" rel="noopener noreferrer nofollow">Embrapa</a>' in html
    assert '<img src="data:image/png;base64,iVBORw0KGgo=" alt="Figura" loading="lazy">' in html
    assert '<span class="highlight">poda</span>' in html and "&lt;tag&gt;" in html
    assert html.endswith("</li></ul></p>")  # tags abertas são fechadas
    assert text.startswith("Olá mundo") and "alert" not in text


def test_parse_question_from_official_example():
    q = parse_question(DOC_ILPF)
    assert q.id == "ilpf_400" and q.number == 400 and q.book == "Integração Lavoura-Pecuária-Floresta" and q.year == 2015
    assert q.answer_html.startswith("<p>Sim,") and q.answer_excerpt.startswith("Sim, a utilização")
    assert q.pdf_url.endswith(".pdf") and q.epub_url.endswith(".epub")
    assert parse_question({"_id": "x"}) is None and parse_question({"_source": {"question": "  "}}) is None


# ------------------------------------------------------------------ RespondeAgro
def test_search_uses_documented_template_oauth_and_parses_hits():
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    result = make_service(api).search("  poda   da videira ", page=2, size=5)

    token_call = api.api_calls("/token")[0]
    assert token_call.headers["authorization"] == "Basic " + base64.b64encode(b"chave-app:segredo-app").decode()
    assert token_call.content == b"grant_type=client_credentials"
    search_call = api.api_calls("/respondeagro/v1/_search/template")[0]
    assert search_call.headers["authorization"] == "Bearer tok-1"
    assert json.loads(search_call.content) == {"id": "query_one_book", "params": {"query_string": "poda da videira", "from": 5, "size": 5, "book_id": "uva"}}

    assert result.total == 24 and result.total_is_exact and result.page == 2 and result.book_id == "uva" and len(result.items) == 2
    first, second = result.items
    assert first.question_html == 'Quando fazer a <span class="highlight">poda</span>?' and "script" not in first.answer_html
    assert first.snippet_html == '<p>A <span class="highlight">poda</span> é feita</p>'
    assert first.epub_url is None and first.pdf_url == "https://exemplo.embrapa.br/uva.pdf"  # só links http(s)
    assert "data:image/png" in second.answer_html and "rastreador" not in second.answer_html and "onclick" not in second.answer_html
    assert "Embrapa" in result.source


def test_search_all_books_uses_query_all():
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    make_service(api).search("irrigação", scope="all")
    body = json.loads(api.api_calls("/respondeagro/v1/_search/template")[0].content)
    assert body["id"] == "query_all" and "book_id" not in body["params"]


def test_token_and_results_are_cached():
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    svc = make_service(api)
    svc.search("poda")
    svc.search("Poda")  # mesma busca (sem diferenciar maiúsculas): cache
    svc.search("colheita")
    assert api.tokens == 1 and len(api.api_calls("/respondeagro/v1/_search/template")) == 2


def test_expired_token_is_renewed_once():
    api = FakeAgroAPI()
    api.reject_token_once = True
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    result = make_service(api).search("poda")
    assert api.tokens == 2 and result.total == 24


def test_invalid_credentials_and_denied_access():
    def invalid_client(request):
        return httpx.Response(401, json={"error_description": "A valid OAuth client could not be found for client_id: x", "error": "invalid_client"})

    with pytest.raises(ExternalServiceError) as exc:
        make_service(invalid_client).search("poda")
    assert exc.value.status_code == 502 and "EMBRAPA_CONSUMER_KEY" in exc.value.message

    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(403, text="<ams:fault><ams:code>900908</ams:code></ams:fault>")
    with pytest.raises(ExternalServiceError) as exc:
        make_service(api).search("poda")
    assert "Subscribe" in exc.value.message


@pytest.mark.parametrize(("status", "expected", "fragment"), [(429, 503, "limite"), (500, 503, "indisponível")])
def test_quota_and_server_errors(status, expected, fragment):
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(status, text="<fault/>")
    with pytest.raises(ExternalServiceError) as exc:
        make_service(api).search("poda")
    assert exc.value.status_code == expected and fragment in exc.value.message


def test_timeout():
    def slow(_request):
        raise httpx.ReadTimeout("t")

    with pytest.raises(ExternalServiceError) as exc:
        make_service(slow).search("poda")
    assert exc.value.status_code == 504


def test_not_configured_never_calls_the_api():
    api = FakeAgroAPI()
    svc = make_service(api, embrapa_consumer_secret="")
    with pytest.raises(ExternalServiceError) as exc:
        svc.search("poda")
    assert exc.value.status_code == 503 and "EMBRAPA_CONSUMER_KEY" in exc.value.message and api.calls == []


def test_previous_result_is_reused_when_agroapi_fails(monkeypatch):
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    svc = make_service(api)
    first = svc.search("poda")
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(503)
    now = external.time.monotonic()
    monkeypatch.setattr(external.time, "monotonic", lambda: now + 2 * 86400)  # cache de 24 h vencido
    assert svc.search("poda") == first


def test_question_by_id_and_not_found():
    api = FakeAgroAPI()
    api.routes[("GET", "/respondeagro/v1/_doc/ilpf_400")] = httpx.Response(200, json=DOC_ILPF)
    api.routes[("GET", "/respondeagro/v1/_doc/uva_999")] = httpx.Response(404, json={"_index": "responde_agro", "_id": "uva_999", "found": False})
    svc = make_service(api)
    assert svc.question("ilpf_400").number == 400
    assert svc.question("uva_999") is None
    assert svc.question("../token") is None  # identificador fora do padrão nem chega à AgroAPI
    assert not api.api_calls("/respondeagro/v1/_doc/../token")


def test_books_from_aggregation():
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json={"hits": {"hits": []}, "aggregations": {"book_ids": {"buckets": [{"key": "abacaxi", "doc_count": 500}, {"key": "uva", "doc_count": 500}]}}})
    assert [(b.id, b.questions) for b in make_service(api).books()] == [("abacaxi", 500), ("uva", 500)]


# ------------------------------------------------------------------ Agritec
def agritec_api() -> FakeAgroAPI:
    api = FakeAgroAPI()
    api.routes[("GET", "/agritec/v2/culturas")] = httpx.Response(200, json=CULTURES)
    api.routes[("GET", "/agritec/v2/zoneamento")] = httpx.Response(200, json=ZARC)
    api.routes[("GET", "/agritec/v2/solos")] = httpx.Response(200, json=SOLOS)
    api.routes[("GET", "/agritec/v2/culturas/7001/informacoes-adicionais")] = httpx.Response(200, json=INFO)
    return api


def test_grape_cultures_are_filtered_by_name():
    cultures = make_service(agritec_api()).grape_cultures()
    assert [(c.id, c.name) for c in cultures] == [(7002, "UVA AMERICANA E HÍBRIDA"), (7001, "UVA EUROPEIA IRRIGADA")]
    assert cultures[1].cultivation == "IRRIGADO" and cultures[1].has_zoning


def test_zarc_windows_soils_and_info():
    api = agritec_api()
    z = make_service(api).zarc(7001, 2611101)
    params = api.api_calls("/agritec/v2/zoneamento")[0].url.params
    assert (params["idCultura"], params["codigoIBGE"], params["risco"]) == ("7001", "2611101", "todos")
    assert z.municipality == "Petrolina" and z.uf == "PE" and z.culture.name == "UVA EUROPEIA IRRIGADA"
    assert [(w.soil, w.risk) for w in z.windows] == [("AD1", 20), ("AD2", 30)]  # janela com datas inválidas descartada
    assert (z.windows[0].start_day, z.windows[0].start_month, z.windows[0].end_day, z.windows[0].end_month) == (11, 3, 30, 6)
    assert z.soils == {"AD1": "AD1: 0.34 até 0.46 (mm/cm)", "AD2": "AD2: 0.46 até 0.61 (mm/cm)"}  # só os solos citados
    assert z.info["portaria"] == "Port. 412 de 30/12/2020" and "Zarc" in z.source


def test_zarc_validations_and_documented_error_body():
    api = agritec_api()
    svc = make_service(api)
    with pytest.raises(ExternalServiceError) as exc:
        svc.zarc(7001, 3550308)  # São Paulo: fora da lista
    assert exc.value.status_code == 422
    with pytest.raises(ExternalServiceError) as exc:
        svc.zarc(101, 2611101)  # abacaxi não é cultura de uva
    assert exc.value.status_code == 404
    api.routes[("GET", "/agritec/v2/zoneamento")] = httpx.Response(400, json={"error": {"status": 400, "code": "dados-invalidos", "summary": "A requisição não pode ser processada.", "fieldErrors": [{"field": "idCultura", "summary": "Identificador da cultura não encontrado."}]}})
    with pytest.raises(ExternalServiceError) as exc:
        svc.zarc(7002, 2611101)
    assert "Identificador da cultura não encontrado." in exc.value.message


# ------------------------------------------------------------------ rotas da API
@pytest.fixture
def embrapa_override():
    def use(api, **overrides):
        svc = make_service(api, **overrides)
        app.dependency_overrides[get_embrapa] = lambda: svc
        return svc

    reset_embrapa_rate_limit()
    yield use
    app.dependency_overrides.pop(get_embrapa, None)
    reset_embrapa_rate_limit()


def test_public_routes(client, embrapa_override):
    api = agritec_api()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    embrapa_override(api)
    res = client.get(f"{API}/embrapa/questions", params={"q": "poda", "pageSize": 4})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 24 and body["pageSize"] == 4 and body["items"][0]["questionHtml"].endswith("?") and body["fetchedAt"]
    assert client.get(f"{API}/embrapa/questions", params={"q": " a "}).status_code == 422
    assert client.get(f"{API}/embrapa/questions", params={"q": "poda", "scope": "outro"}).status_code == 422
    assert client.get(f"{API}/embrapa/zarc/cultures").json()[0]["hasZoning"] is True
    zarc = client.get(f"{API}/embrapa/zarc", params={"cultureId": 7001, "ibge": 2611101}).json()
    assert zarc["windows"][0]["startMonth"] == 3 and zarc["ibgeCode"] == 2611101
    assert client.get(f"{API}/embrapa/zarc", params={"cultureId": 7001, "ibge": 12}).status_code == 422
    assert client.get(f"{API}/embrapa/questions/uva_404").status_code == 404
    munis = client.get(f"{API}/embrapa/municipalities").json()
    assert {"ibgeCode": 2611101, "name": "Petrolina", "uf": "PE"} in munis and len(munis) == 8


def test_route_not_configured_and_status_without_secrets(client, embrapa_override):
    embrapa_override(FakeAgroAPI(), embrapa_consumer_key="", embrapa_consumer_secret="")
    res = client.get(f"{API}/embrapa/questions", params={"q": "poda"})
    assert res.status_code == 503 and "EMBRAPA_CONSUMER_KEY" in res.json()["detail"]
    status = client.get(f"{API}/public/integrations").json()
    assert status["embrapa"]["configured"] is False and status["detector"]["configured"] is True and status["yoloLoaded"] is False

    embrapa_override(FakeAgroAPI())
    text = client.get(f"{API}/public/integrations").text
    assert '"configured":true' in text.replace(" ", "") and "chave-app" not in text and "segredo-app" not in text


def test_public_routes_are_rate_limited(client, embrapa_override, monkeypatch):
    from app.routers import integrations

    monkeypatch.setattr(integrations._embrapa_limiter, "limit", 3)
    api = FakeAgroAPI()
    api.routes[("POST", "/respondeagro/v1/_search/template")] = httpx.Response(200, json=SEARCH)
    embrapa_override(api)
    codes = [client.get(f"{API}/embrapa/questions", params={"q": "poda"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
