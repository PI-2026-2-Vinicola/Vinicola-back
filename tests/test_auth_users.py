from app.routers.auth import reset_login_attempts

from .conftest import ADMIN, API, login


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.json()["status"] == "ok"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_data_requires_login(client):
    for path in ("/sensors", "/readings", "/stats/summary"):
        assert client.get(API + path).status_code == 401


def test_catalog_and_public_overview_are_open(client):
    assert len(client.get(f"{API}/varieties").json()) == 6
    overview = client.get(f"{API}/public/overview").json()
    assert {"sensorsTotal", "analyses30d", "lastAnalysisAt"} <= overview.keys()


def test_login_me_and_wrong_password(client):
    reset_login_attempts()
    bad = client.post(f"{API}/auth/login", json={"email": ADMIN[0], "password": "errada123"})
    assert bad.status_code == 401
    headers = login(client, *ADMIN)
    me = client.get(f"{API}/auth/me", headers=headers).json()
    assert me["role"] == "admin" and me["lastLoginAt"]


def test_login_lockout(client):
    reset_login_attempts()
    for _ in range(5):
        assert client.post(f"{API}/auth/login", json={"email": "ninguem@teste.com", "password": "x1234567"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": "ninguem@teste.com", "password": "x1234567"}).status_code == 429
    reset_login_attempts()


def test_invalid_token(client):
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer abc"}).status_code == 401


def test_users_admin_only(client, admin_headers, operador_headers):
    assert client.get(f"{API}/users", headers=operador_headers).status_code == 403
    users = client.get(f"{API}/users", headers=admin_headers).json()
    assert any(u["email"] == ADMIN[0] for u in users)


def test_create_user_validation(client, admin_headers):
    weak = client.post(f"{API}/users", json={"name": "Fraca", "email": "fraca@teste.com", "role": "operador", "password": "123"}, headers=admin_headers)
    assert weak.status_code == 422 and "senha" in weak.json()["detail"].lower()
    bad_email = client.post(f"{API}/users", json={"name": "X Y", "email": "sem-arroba", "role": "operador", "password": "Senha2026x"}, headers=admin_headers)
    assert bad_email.status_code == 422


def test_admin_cannot_lock_themselves_out(client, admin_headers):
    me = client.get(f"{API}/auth/me", headers=admin_headers).json()
    res = client.patch(f"{API}/users/{me['id']}", json={"isActive": False}, headers=admin_headers)
    assert res.status_code == 400
    res = client.patch(f"{API}/users/{me['id']}", json={"role": "operador"}, headers=admin_headers)
    assert res.status_code == 400


def test_deactivated_user_loses_access(client, admin_headers):
    created = client.post(f"{API}/users", json={"name": "Temporário", "email": "temp@teste.com", "role": "operador", "password": "Senha2026x"}, headers=admin_headers)
    assert created.status_code == 201
    headers = login(client, "temp@teste.com", "Senha2026x")
    client.patch(f"{API}/users/{created.json()['id']}", json={"isActive": False}, headers=admin_headers)
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401
    reset_login_attempts()
    assert client.post(f"{API}/auth/login", json={"email": "temp@teste.com", "password": "Senha2026x"}).status_code == 401


def test_change_password(client, admin_headers):
    client.post(f"{API}/users", json={"name": "Troca Senha", "email": "troca@teste.com", "role": "gestor", "password": "Senha2026x"}, headers=admin_headers)
    headers = login(client, "troca@teste.com", "Senha2026x")
    wrong = client.post(f"{API}/auth/password", json={"currentPassword": "errada99", "newPassword": "NovaSenha1"}, headers=headers)
    assert wrong.status_code == 400
    ok = client.post(f"{API}/auth/password", json={"currentPassword": "Senha2026x", "newPassword": "NovaSenha1"}, headers=headers)
    assert ok.status_code == 204
    login(client, "troca@teste.com", "NovaSenha1")
