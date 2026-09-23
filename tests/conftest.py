import os
import tempfile

import pytest

# Configura um banco e um diretório de imagens temporários antes de importar a aplicação.
_tmp = tempfile.mkdtemp(prefix="osais-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["SEED_DEMO"] = "true"
os.environ["PUBLIC_READ"] = "true"
os.environ["OSAIS_DETECTOR"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def token_for(client, email: str) -> str:
    res = client.post("/api/v1/auth/login", json={"email": email, "password": "osais2026"})
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


@pytest.fixture(scope="session")
def admin_headers(client):
    return {"Authorization": f"Bearer {token_for(client, 'admin@osais.agr.br')}"}


@pytest.fixture(scope="session")
def operador_headers(client):
    return {"Authorization": f"Bearer {token_for(client, 'operador@osais.agr.br')}"}
