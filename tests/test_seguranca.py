"""
Testes dos controles de segurança: autenticação, RBAC pela matriz, isolamento entre organizações,
hash de senha, segredos fora do código e migração do banco.
"""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.database import SessionLocal, ensure_columns, ensure_role_constraint
from app.models import AuditLog, User
from app.rbac import MATRIX, allowed
from app.routers.auth import reset_login_attempts
from app.security import verify_secret

from .conftest import API, login
from .images import grape_cluster, jpeg

ROOT = Path(__file__).resolve().parents[1]
SENHA = "SenhaSegura2026"


# ------------------------------------------------------------------ cenário: duas exportadoras
@pytest.fixture(scope="module")
def cenario(client, admin_headers):
    """Exportadoras A e B; gestor e operador em A; gestor em B; um sensor em cada e uma leitura em B."""
    orgs = {}
    for key in ("A", "B"):
        res = client.post(f"{API}/organizations", json={"name": f"Exportadora Teste {key}", "city": "Petrolina"}, headers=admin_headers)
        assert res.status_code == 201, res.text
        orgs[key] = res.json()["id"]
    usuarios = {}
    for nome, role, org in (("gestorA", "gestor", "A"), ("operadorA", "operador", "A"), ("gestorB", "gestor", "B")):
        body = {"name": nome, "email": f"{nome.lower()}@seguranca.example", "role": role, "password": SENHA, "organizationId": orgs[org]}
        res = client.post(f"{API}/users", json=body, headers=admin_headers)
        assert res.status_code == 201, res.text
        usuarios[nome] = login(client, body["email"], SENHA)
    tokens = {}
    for key in ("A", "B"):
        body = {"id": f"SEG-{key}", "name": f"Câmera {key}", "block": f"Talhão {key}", "location": "Fileira 1", "varietyId": "syrah", "organizationId": orgs[key]}
        res = client.post(f"{API}/sensors", json=body, headers=admin_headers)
        assert res.status_code == 201, res.text
        tokens[key] = res.json()["deviceToken"]
    res = client.post(f"{API}/ingest", data={"sensor_id": "SEG-B"}, files={"image": ("b.jpg", jpeg(grape_cluster()), "image/jpeg")},
                      headers={"X-Device-Token": tokens["B"]})
    assert res.status_code == 201, res.text
    return {"orgs": orgs, "u": usuarios, "leitura_B": res.json()["id"]}


def _negacoes(antes: int) -> int:
    with SessionLocal() as db:
        return db.query(AuditLog).filter(AuditLog.action == "acesso_negado").count() - antes


def _total_negacoes() -> int:
    return _negacoes(0)


# ------------------------------------------------------------------ 1. autenticação
def test_login_valido_cria_sessao_com_perfil_organizacao_e_permissoes(client, cenario):
    reset_login_attempts()
    ok = client.post(f"{API}/auth/login", json={"email": "gestora@seguranca.example", "password": SENHA})
    assert ok.status_code == 200, ok.text
    user = ok.json()["user"]
    assert user["role"] == "gestor" and user["organizationName"] == "Exportadora Teste A" and user["roleLabel"] == "Exportadora (gestor)"
    assert user["permissions"]["sensores"] == "CRUD" and user["permissions"]["usuarios"] == ""
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {ok.json()['accessToken']}"})
    assert me.status_code == 200 and me.json()["email"] == "gestora@seguranca.example"


def test_login_invalido_e_rotas_protegidas_sem_sessao(client, cenario):
    reset_login_attempts()
    assert client.post(f"{API}/auth/login", json={"email": "gestora@seguranca.example", "password": "SenhaErrada2026"}).status_code == 401
    assert client.get(f"{API}/sensors").status_code == 401
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer token.adulterado.invalido"}).status_code == 401
    reset_login_attempts()


# ------------------------------------------------------------------ 2. RBAC: a matriz vale na API
LIST_ENDPOINT = {"usuarios": "/users", "organizacoes": "/organizations", "sensores": "/sensors", "leituras": "/readings",
                 "importacoes": "/imports", "auditoria": "/audit", "sistema": "/system/status"}


@pytest.mark.parametrize("perfil", ["gestorA", "operadorA"])
@pytest.mark.parametrize("recurso", list(LIST_ENDPOINT))
def test_consulta_de_cada_recurso_segue_a_matriz(client, cenario, perfil, recurso):
    role = {"gestorA": "gestor", "operadorA": "operador"}[perfil]
    res = client.get(API + LIST_ENDPOINT[recurso], headers=cenario["u"][perfil])
    assert res.status_code == (200 if allowed(role, recurso, "R") else 403), (perfil, recurso, res.text)


def test_acesso_permitido_e_negado_por_perfil_com_auditoria(client, cenario):
    antes = _total_negacoes()
    sensor = {"id": "SEG-A2", "name": "Câmera A2", "block": "A", "location": "Fileira 2", "varietyId": "syrah"}
    # permitido: gestor cadastra sensor na própria exportadora
    assert client.post(f"{API}/sensors", json=sensor, headers=cenario["u"]["gestorA"]).status_code == 201
    # negado: operador não cadastra sensor; operador não vê importações; gestor não cria usuários
    negado = client.post(f"{API}/sensors", json=sensor | {"id": "SEG-A3"}, headers=cenario["u"]["operadorA"])
    assert negado.status_code == 403 and "Acesso negado" in negado.json()["detail"]
    assert client.get(f"{API}/imports", headers=cenario["u"]["operadorA"]).status_code == 403
    assert client.post(f"{API}/users", json={"name": "Novo", "email": "novo@x.example", "role": "admin", "password": SENHA}, headers=cenario["u"]["gestorA"]).status_code == 403
    assert _negacoes(antes) == 3


# ------------------------------------------------------------------ 3. isolamento entre organizações
def test_exportadora_A_nao_acessa_dados_da_exportadora_B(client, cenario):
    a, antes = cenario["u"]["gestorA"], _total_negacoes()
    assert {s["id"] for s in client.get(f"{API}/sensors", headers=a).json()} <= {"SEG-A", "SEG-A2"}
    assert all(r["sensorId"] != "SEG-B" for r in client.get(f"{API}/readings", headers=a).json()["items"])
    assert client.get(f"{API}/stats/summary", headers=a).json()["readings"] == 0
    for method, path in (
        ("get", "/sensors/SEG-B"),
        ("patch", "/sensors/SEG-B"),
        ("get", f"/readings/{cenario['leitura_B']}"),
        ("get", f"/readings/{cenario['leitura_B']}/image"),
        ("get", "/sensors/SEG-B/environment"),
        ("delete", f"/readings/{cenario['leitura_B']}"),
        ("get", f"/organizations/{cenario['orgs']['B']}"),
    ):
        res = getattr(client, method)(API + path, headers=a, **({"json": {}} if method == "patch" else {}))
        assert res.status_code == 403 and "outra organização" in res.json()["detail"], (path, res.text)
    assert _negacoes(antes) == 7


def test_cadastro_e_envio_nao_cruzam_organizacoes(client, cenario):
    novo_em_B = {"id": "SEG-X", "name": "Intrusa", "block": "B", "location": "L", "varietyId": "syrah", "organizationId": cenario["orgs"]["B"]}
    assert client.post(f"{API}/sensors", json=novo_em_B, headers=cenario["u"]["gestorA"]).status_code == 403
    foto = {"image": ("a.jpg", jpeg(grape_cluster()), "image/jpeg")}
    assert client.post(f"{API}/ingest", data={"sensor_id": "SEG-B"}, files=foto, headers=cenario["u"]["operadorA"]).status_code == 403
    ok = client.post(f"{API}/ingest", data={"sensor_id": "SEG-A"}, files={"image": ("a.jpg", jpeg(grape_cluster()), "image/jpeg")}, headers=cenario["u"]["operadorA"])
    assert ok.status_code == 201 and ok.json()["organizationName"] == "Exportadora Teste A"
    assert client.get(f"{API}/readings/{ok.json()['id']}", headers=cenario["u"]["gestorB"]).status_code == 403


# ------------------------------------------------------------------ 4. senhas protegidas
def test_senha_armazenada_como_hash_pbkdf2_com_sal(client, cenario):
    with SessionLocal() as db:
        hashes = [u.password_hash for u in db.query(User).filter(User.email.like("%@seguranca.example")).all()]
    assert len(hashes) == 3 and len(set(hashes)) == 3  # mesma senha, hashes diferentes (sal aleatório)
    for h in hashes:
        assert h.startswith("pbkdf2_sha256$240000$") and SENHA not in h
        assert verify_secret(SENHA, h) and not verify_secret("outra", h)


# ------------------------------------------------------------------ 5. segredos fora do código
def test_nenhum_arquivo_de_segredos_versionado():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not tracked:
        pytest.skip("fora de um repositório git")
    assert not [f for f in tracked if Path(f).name == ".env" or f.endswith((".pem", ".key"))]
    assert ".env" in (ROOT / ".gitignore").read_text().split()
    example = (ROOT / ".env.example").read_text()
    for name in ("JWT_SECRET", "POSTGRES_PASSWORD", "S3_SECRET_ACCESS_KEY"):
        assert f"{name}=\n" in example  # modelo sem valores reais


# ------------------------------------------------------------------ 7. migração de bancos antigos
def test_banco_antigo_recebe_organizacoes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/antigo.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(160), role VARCHAR(20) NOT NULL, "
                          "CONSTRAINT ck_users_role CHECK (role IN ('admin','gestor','operador')))"))
        conn.execute(text("CREATE TABLE sensors (id VARCHAR(20) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO users (id, email, role) VALUES (1, 'a@b.example', 'gestor')"))
    assert {"users.organization_id", "sensors.organization_id"} <= set(ensure_columns(engine))
    assert ensure_role_constraint(engine) is False  # restrição já está certa
    with engine.connect() as conn:
        assert conn.execute(text("SELECT role FROM users WHERE id = 1")).scalar() == "gestor"  # dados preservados


def test_banco_com_o_perfil_cliente_removido_e_ajustado(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/com_cliente.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(160), role VARCHAR(20) NOT NULL, is_active BOOLEAN, "
                          "CONSTRAINT ck_users_role CHECK (role IN ('admin','gestor','operador','cliente')))"))
        conn.execute(text("INSERT INTO users VALUES (1, 'g@b.example', 'gestor', 1), (2, 'c@b.example', 'cliente', 1)"))
    assert ensure_role_constraint(engine) is True and ensure_role_constraint(engine) is False
    with engine.begin() as conn:
        assert conn.execute(text("SELECT role, is_active FROM users WHERE id = 2")).one() == ("operador", 0)  # desativado
        assert conn.execute(text("SELECT role FROM users WHERE id = 1")).scalar() == "gestor"
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO users (id, email, role) VALUES (3, 'x@b.example', 'cliente')"))


def test_matriz_cobre_todos_os_perfis_e_operacoes_validas():
    for recurso, (_, perms, _) in MATRIX.items():
        assert set(perms) <= {"admin", "gestor", "operador"}
        assert all(set(ops) <= set("CRUD") for ops in perms.values()), recurso
    assert MATRIX["usuarios"][1] == {"admin": "CRUD"}


def test_matriz_exposta_pela_api_e_a_mesma_aplicada(client, cenario):
    data = client.get(f"{API}/auth/matrix", headers=cenario["u"]["operadorA"]).json()
    recursos = {r["id"]: r["permissions"] for r in data["resources"]}
    assert recursos == {k: v[1] for k, v in MATRIX.items()} and [r["id"] for r in data["roles"]] == ["admin", "gestor", "operador"]


def test_administrador_inicial_e_criado_mesmo_com_usuarios_de_organizacoes(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.seed import bootstrap_admin

    engine = create_engine(f"sqlite:///{tmp_path}/novo.db")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(User(name="Gestor", email="g@x.example", role="gestor", password_hash="x"))
        db.commit()
        bootstrap_admin(db)
        assert db.query(User).filter(User.role == "admin").count() == 1
        assert bootstrap_admin(db) is None and db.query(User).filter(User.role == "admin").count() == 1
