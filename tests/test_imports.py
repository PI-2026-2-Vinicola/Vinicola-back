import io
import json

from openpyxl import Workbook

from .conftest import API
from .images import grape_cluster, jpeg

CSV_HEADER = "sensor_id;captured_at;variety_id;quality;confidence;maturation;classification;observations\n"


def _send(client, headers, kind, name, content, endpoint="/imports/preview", **form):
    return client.post(f"{API}{endpoint}", data={"kind": kind, **form}, files={"file": (name, content)}, headers=headers)


def test_templates(client, gestor_headers, operador_headers):
    res = client.get(f"{API}/imports/templates/readings", headers=gestor_headers)
    assert res.status_code == 200 and "sensor_id;captured_at" in res.content.decode("utf-8-sig")
    assert client.get(f"{API}/imports/templates/readings", headers=operador_headers).status_code == 403


def test_readings_preview_validates_each_row(client, gestor_headers, sensor):
    existing = client.post(f"{API}/ingest", data={"sensor_id": "T-001", "captured_at": "2026-08-14T09:00:00-03:00"}, files={"image": ("c.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=gestor_headers)
    assert existing.status_code == 201
    rows = [
        "T-001;15/08/2026 08:00;syrah;Boa;93,5;Adequada;;Cacho uniforme",  # válida
        "T-001;15/08/2026 10:00;Syrah;atencao;0.81;pintor;EM OBSERVAÇÃO;",  # válida (nome da variedade, confiança 0–1)
        "T-001;15/08/2026 12:00;syrah;otima;90;;;",  # qualidade inválida
        "X-404;15/08/2026 12:00;syrah;boa;90;;;",  # sensor inexistente
        "T-001;15/08/2099 12:00;syrah;boa;90;;;",  # data futura
        "T-001;15/08/2026 08:00;syrah;boa;90;;;",  # repetida no arquivo
        "T-001;15/08/2026 14:00;syrah;boa;90;;REVISÃO NECESSÁRIA;",  # classificação incoerente
        "T-001;14/08/2026 09:00;syrah;boa;90;;;",  # já existe no banco (ingestão)
    ]
    content = (CSV_HEADER + "\n".join(rows)).encode()
    res = _send(client, gestor_headers, "readings", "historico.csv", content)
    assert res.status_code == 200, res.text
    p = res.json()
    assert p["total"] == 8 and p["valid"] == 2 and p["invalid"] == 5 and p["duplicates"] == 1
    assert {e["row"] for e in p["errors"]} == {4, 5, 6, 7, 8}
    assert p["sample"][0]["confidence"] == 0.935
    # nada foi gravado na pré-visualização
    assert client.get(f"{API}/readings", params={"source": "importacao"}, headers=gestor_headers).json()["total"] == 0


def test_readings_import_and_reimport(client, gestor_headers, sensor):
    content = (CSV_HEADER + "T-001;16/08/2026 08:00;syrah;boa;95;adequada;;\nT-001;16/08/2026 10:00;syrah;critica;88;;;Podridão no cacho\nT-001;16/08/2026 12:00;syrah;ruim_demais;88;;;\n").encode()
    res = _send(client, gestor_headers, "readings", "agosto.csv", content, endpoint="/imports")
    assert res.status_code == 201, res.text
    job = res.json()
    assert job["inserted"] == 2 and job["invalid"] == 1 and job["status"] == "parcial" and job["createdByName"] == "Gestora Teste"
    imported = client.get(f"{API}/readings", params={"source": "importacao", "dateFrom": "2026-08-16", "dateTo": "2026-08-16"}, headers=gestor_headers).json()
    assert imported["total"] == 2 and imported["items"][0]["modelVersion"] == "Importado"
    again = _send(client, gestor_headers, "readings", "agosto.csv", content, endpoint="/imports").json()
    assert again["inserted"] == 0 and again["duplicates"] == 2
    updated = _send(client, gestor_headers, "readings", "agosto.csv", content.replace(b"Podrid", b"Podrid"), endpoint="/imports", onDuplicate="update").json()
    assert updated["updated"] == 2
    jobs = client.get(f"{API}/imports", headers=gestor_headers).json()
    assert jobs[0]["id"] == updated["id"] and jobs[0]["errors"][0]["row"] == 4


def test_sensors_import_from_excel(client, admin_headers):
    wb = Workbook()
    ws = wb.active
    ws.append(["Código", "Nome", "Bloco", "Localização", "Latitude", "Longitude", "Variedade", "Intervalo de captura", "Instalado em"])
    ws.append(["S-100", "Câmera Talhão 1", "Bloco 1", "Fileira 4", "-9,3921", "-40,5012", "Syrah", 90, "10/03/2026"])
    ws.append(["S-101", "Câmera Talhão 2", "Bloco 1", "Fileira 9", None, None, "Chenin Blanc", None, None])
    ws.append(["S-102", "Sem variedade", "Bloco 2", "Fileira 1", 0, 0, "", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    res = _send(client, admin_headers, "sensors", "sensores.xlsx", buf.getvalue(), endpoint="/imports")
    assert res.status_code == 201, res.text
    job = res.json()
    assert job["inserted"] == 2 and job["invalid"] == 1
    s = client.get(f"{API}/sensors/S-100", headers=admin_headers).json()
    assert s["lat"] == -9.3921 and s["varietyId"] == "syrah" and s["installedAt"] == "2026-03-10" and s["status"] == "offline"
    assert client.get(f"{API}/sensors/S-101", headers=admin_headers).json()["hasValidLocation"] is False


def test_environment_import_from_json(client, admin_headers, sensor):
    items = [
        {"sensor_id": "T-001", "measured_at": "2026-09-10T09:00:00-03:00", "temperatura": 28.4, "umidade": 51},
        {"sensor_id": "T-001", "measured_at": "2026-09-10T15:00:00-03:00", "temperatura": 33.1, "umidade": 38, "luminosidade": 81000},
        {"sensor_id": "T-001", "measured_at": "2026-09-11T09:00:00-03:00"},
        {"sensor_id": "T-001", "measured_at": "2026-09-11T10:00:00-03:00", "umidade": 140},
    ]
    res = _send(client, admin_headers, "environment", "clima.json", json.dumps({"items": items}).encode(), endpoint="/imports")
    job = res.json()
    assert job["inserted"] == 2 and job["invalid"] == 2
    days = client.get(f"{API}/stats/environment", params={"sensorId": "T-001", "dateFrom": "2026-09-10", "dateTo": "2026-09-10"}, headers=admin_headers).json()
    assert len(days) == 1 and days[0]["measurements"] == 2 and days[0]["maxTemperatureC"] == 33.1


def test_file_level_errors_are_logged(client, gestor_headers):
    res = _send(client, gestor_headers, "readings", "errado.csv", b"coluna_a;coluna_b\n1;2\n", endpoint="/imports")
    assert res.status_code == 422 and "obrigatórias" in res.json()["detail"]
    assert client.get(f"{API}/imports", headers=gestor_headers).json()[0]["status"] == "falhou"
    assert _send(client, gestor_headers, "readings", "vazio.csv", b"").status_code == 422
    assert _send(client, gestor_headers, "readings", "x.pdf", b"%PDF-1.4").status_code == 422
    assert _send(client, gestor_headers, "outro", "x.csv", b"a;b\n").status_code == 422
