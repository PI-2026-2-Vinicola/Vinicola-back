import os
import tempfile

import pytest

# Banco e diretório de imagens temporários, configurados antes de importar a aplicação.
_tmp = tempfile.mkdtemp(prefix="oasis-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["PUBLIC_READ"] = "false"
os.environ["OASIS_DETECTOR"] = "color"
os.environ["OASIS_ADMIN_EMAIL"] = "admin@oasis.agr.br"
os.environ["OASIS_ADMIN_PASSWORD"] = "Admin2026x"
os.environ["ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers.auth import reset_login_attempts  # noqa: E402

from .images import grape_cluster, jpeg  # noqa: E402,F401

ADMIN = ("admin@oasis.agr.br", "Admin2026x")
API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def login(client, email: str, password: str) -> dict:
    reset_login_attempts()
    res = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['accessToken']}"}


@pytest.fixture(scope="session")
def admin_headers(client):
    return login(client, *ADMIN)


def _user(client, admin_headers, name, email, role):
    res = client.post(f"{API}/users", json={"name": name, "email": email, "role": role, "password": "Senha2026x"}, headers=admin_headers)
    assert res.status_code in (201, 409), res.text
    return login(client, email, "Senha2026x")


@pytest.fixture(scope="session")
def gestor_headers(client, admin_headers):
    return _user(client, admin_headers, "Gestora Teste", "gestora@teste.com", "gestor")


@pytest.fixture(scope="session")
def operador_headers(client, admin_headers):
    return _user(client, admin_headers, "Operador Teste", "operador@teste.com", "operador")


@pytest.fixture(scope="session")
def sensor(client, admin_headers):
    """Sensor real cadastrado pela API; devolve (dados, token do dispositivo)."""
    body = {
        "id": "T-001", "name": "Câmera Teste 01", "block": "Bloco Teste", "location": "Fileira 3",
        "lat": -9.39, "lng": -40.5, "varietyId": "syrah", "device": "ESP32-CAM", "captureIntervalMin": 60,
    }
    res = client.post(f"{API}/sensors", json=body, headers=admin_headers)
    assert res.status_code == 201, res.text
    data = res.json()
    return data["sensor"], data["deviceToken"]
