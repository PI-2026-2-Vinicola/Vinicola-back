"""Itens de produção: saúde com banco, cabeçalhos de segurança, IP do cliente e validação do .env."""


from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app import main as main_module
from app.config import Settings
from app.routers.auth import reset_login_attempts

from .conftest import API

STRONG_SECRET = "s" * 48


def test_health_reports_database(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"


def test_health_returns_503_when_the_database_is_down(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "engine", create_engine(f"sqlite:///{tmp_path}/nao-existe/banco.db"))
    res = client.get("/health")
    assert res.status_code == 503 and res.json()["database"] == "erro"


def test_api_responses_forbid_scripts_and_framing(client, admin_headers):
    for res in (client.get("/health"), client.get(f"{API}/sensors", headers=admin_headers), client.get(f"{API}/sensors")):
        assert res.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_hsts_is_sent_only_over_https(client):
    assert "strict-transport-security" not in client.get("/health").headers
    https = TestClient(main_module.app, base_url="https://testserver").get("/health")
    assert https.headers["strict-transport-security"].startswith("max-age=31536000")


def test_forged_forwarded_for_does_not_bypass_the_ip_lockout(client):
    reset_login_attempts()
    for i in range(5):
        res = client.post(f"{API}/auth/login", json={"email": f"spray{i}@teste.com", "password": "x1234567"}, headers={"X-Forwarded-For": f"203.0.113.{i}"})
        assert res.status_code == 401
    # outro e-mail e outro IP forjado: o bloqueio continua, porque o IP vem da conexão e não do cabeçalho
    res = client.post(f"{API}/auth/login", json={"email": "spray9@teste.com", "password": "x1234567"}, headers={"X-Forwarded-For": "198.51.100.9"})
    assert res.status_code == 429
    reset_login_attempts()


def _prod(**overrides) -> Settings:
    values = {"environment": "production", "jwt_secret": STRONG_SECRET, "public_read": False, "oasis_admin_password": "",
              "cors_origins": "https://oasis.example.com", "database_url": "postgresql+psycopg://oasis:x@db:5432/oasis"}
    return Settings(_env_file=None, **(values | overrides))


def test_production_rejects_a_short_admin_password():
    assert _prod().validate_for_production() == []
    assert any("OASIS_ADMIN_PASSWORD" in p for p in _prod(oasis_admin_password="Curta2026").validate_for_production())
    assert _prod(oasis_admin_password="UmaSenhaLonga2026").validate_for_production() == []


def test_production_warns_about_development_leftovers():
    assert _prod().production_warnings() == []
    warnings = _prod(cors_origins="http://localhost:5173,https://oasis.example.com", database_url="sqlite:///./oasis.db").production_warnings()
    assert len(warnings) == 2 and "localhost:5173" in warnings[0] and "oasis.example.com" not in warnings[0]
