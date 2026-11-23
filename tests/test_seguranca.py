"""
Testes dos controles de segurança: autenticação, RBAC pela matriz, isolamento entre organizações,
hash de senha, criptografia dos dados sensíveis, segredos fora do código e migração do banco.
"""

import base64
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app import crypto
from app.config import get_settings
from app.crypto import DecryptionError, decrypt, encrypt, key_problem
from app.database import SessionLocal, ensure_columns, ensure_role_constraint
from app.models import AuditLog, InternationalClient, User
from app.rbac import MATRIX, allowed
from app.routers.auth import reset_login_attempts
from app.routers.clients import context
from app.security import verify_secret

from .conftest import API, login
from .images import grape_cluster, jpeg

ROOT = Path(__file__).resolve().parents[1]
SENHA = "SenhaSegura2026"


# ------------------------------------------------------------------ cenário: duas exportadoras
@pytest.fixture(scope="module")
def cenario(client, admin_headers):
    """Exportadoras A e B; gestor, operador e cliente em A; gestor em B; sensores, uma leitura em B e clientes."""
    orgs = {}
    for key in ("A", "B"):
        res = client.post(f"{API}/organizations", json={"name": f"Exportadora Teste {key}", "city": "Petrolina"}, headers=admin_headers)
        assert res.status_code == 201, res.text
        orgs[key] = res.json()["id"]
    clientes = {}
    for key, empresa in (("A", "Importadora Alfa GmbH"), ("A2", "Importadora Gama SA"), ("B", "Importadora Beta Ltd")):
        body = {"companyName": empresa, "country": "Alemanha", "contactName": "Anna Schmidt", "email": f"anna.{key.lower()}@importadora.example",
                "phone": "+49 30 555 0101", "taxId": f"DE1234567{len(key)}", "commercialTerms": "40 t FOB Pecém, US$ 2,10/kg", "organizationId": orgs[key[0]]}
        res = client.post(f"{API}/clients", json=body, headers=admin_headers)
        assert res.status_code == 201, res.text
        clientes[key] = res.json()["id"]
    usuarios = {}
    for nome, role, org, cli in (("gestorA", "gestor", "A", None), ("operadorA", "operador", "A", None), ("clienteA", "cliente", "A", "A"), ("gestorB", "gestor", "B", None)):
        body = {"name": nome, "email": f"{nome.lower()}@seguranca.example", "role": role, "password": SENHA,
                "organizationId": orgs[org], "clientId": clientes[cli] if cli else None}
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
    return {"orgs": orgs, "clientes": clientes, "u": usuarios, "leitura_B": res.json()["id"]}


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
    assert user["permissions"]["clientes"] == "CRUD" and user["permissions"]["usuarios"] == ""
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {ok.json()['accessToken']}"})
    assert me.status_code == 200 and me.json()["email"] == "gestora@seguranca.example"


def test_login_invalido_e_rotas_protegidas_sem_sessao(client, cenario):
    reset_login_attempts()
    assert client.post(f"{API}/auth/login", json={"email": "gestora@seguranca.example", "password": "SenhaErrada2026"}).status_code == 401
    assert client.get(f"{API}/clients").status_code == 401
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer token.adulterado.invalido"}).status_code == 401
    reset_login_attempts()


# ------------------------------------------------------------------ 2. RBAC: a matriz vale na API
LIST_ENDPOINT = {"usuarios": "/users", "organizacoes": "/organizations", "sensores": "/sensors", "leituras": "/readings",
                 "importacoes": "/imports", "clientes": "/clients", "auditoria": "/audit", "sistema": "/system/status"}


@pytest.mark.parametrize("perfil", ["gestorA", "operadorA", "clienteA"])
@pytest.mark.parametrize("recurso", list(LIST_ENDPOINT))
def test_consulta_de_cada_recurso_segue_a_matriz(client, cenario, perfil, recurso):
    role = {"gestorA": "gestor", "operadorA": "operador", "clienteA": "cliente"}[perfil]
    res = client.get(API + LIST_ENDPOINT[recurso], headers=cenario["u"][perfil])
    assert res.status_code == (200 if allowed(role, recurso, "R") else 403), (perfil, recurso, res.text)


def test_acesso_permitido_e_negado_por_perfil_com_auditoria(client, cenario):
    antes = _total_negacoes()
    # permitido: gestor cadastra cliente internacional da própria exportadora
    novo = {"companyName": "Importadora Delta Oy", "country": "Finlândia", "email": "compras@delta.example"}
    assert client.post(f"{API}/clients", json=novo, headers=cenario["u"]["gestorA"]).status_code == 201
    # negado: operador não acessa clientes; cliente não cria sensor; gestor não cria usuários
    negado = client.get(f"{API}/clients", headers=cenario["u"]["operadorA"])
    assert negado.status_code == 403 and "Acesso negado" in negado.json()["detail"]
    assert client.post(f"{API}/sensors", json={"id": "X-1", "name": "X", "block": "B", "location": "L", "varietyId": "syrah"}, headers=cenario["u"]["clienteA"]).status_code == 403
    assert client.post(f"{API}/users", json={"name": "Novo", "email": "novo@x.example", "role": "admin", "password": SENHA}, headers=cenario["u"]["gestorA"]).status_code == 403
    assert _negacoes(antes) == 3


# ------------------------------------------------------------------ 3. isolamento entre organizações
def test_exportadora_A_nao_acessa_dados_da_exportadora_B(client, cenario):
    a, antes = cenario["u"]["gestorA"], _total_negacoes()
    assert {s["id"] for s in client.get(f"{API}/sensors", headers=a).json()} == {"SEG-A"}
    assert all(r["sensorId"] != "SEG-B" for r in client.get(f"{API}/readings", headers=a).json()["items"])
    assert client.get(f"{API}/stats/summary", headers=a).json()["readings"] == 0
    for method, path in (
        ("get", "/sensors/SEG-B"),
        ("patch", "/sensors/SEG-B"),
        ("get", f"/readings/{cenario['leitura_B']}"),
        ("get", f"/readings/{cenario['leitura_B']}/image"),
        ("get", f"/clients/{cenario['clientes']['B']}"),
        ("patch", f"/clients/{cenario['clientes']['B']}"),
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


def test_cliente_internacional_so_ve_o_proprio_cadastro(client, cenario):
    c = cenario["u"]["clienteA"]
    lista = client.get(f"{API}/clients", headers=c).json()
    assert [x["id"] for x in lista] == [cenario["clientes"]["A"]]
    proprio = client.get(f"{API}/clients/{cenario['clientes']['A']}", headers=c)
    assert proprio.status_code == 200 and proprio.json()["email"] == "anna.a@importadora.example"
    outro_da_mesma_exportadora = client.get(f"{API}/clients/{cenario['clientes']['A2']}", headers=c)
    assert outro_da_mesma_exportadora.status_code == 403 and "próprio cadastro" in outro_da_mesma_exportadora.json()["detail"]
    assert client.patch(f"{API}/clients/{cenario['clientes']['A']}", json={"phone": "1"}, headers=c).status_code == 403  # só leitura


# ------------------------------------------------------------------ 4. senhas protegidas
def test_senha_armazenada_como_hash_pbkdf2_com_sal(client, cenario):
    with SessionLocal() as db:
        hashes = [u.password_hash for u in db.query(User).filter(User.email.like("%@seguranca.example")).all()]
    assert len(hashes) == 4 and len(set(hashes)) == 4  # mesma senha, hashes diferentes (sal aleatório)
    for h in hashes:
        assert h.startswith("pbkdf2_sha256$240000$") and SENHA not in h
        assert verify_secret(SENHA, h) and not verify_secret("outra", h)


# ------------------------------------------------------------------ 5. dados sensíveis cifrados
def test_dados_do_cliente_internacional_ficam_cifrados_no_banco(client, admin_headers, cenario):
    cid = cenario["clientes"]["A"]
    with SessionLocal() as db:
        row = db.get(InternationalClient, cid)
        raw = {f: getattr(row, f"{f}_enc") for f in ("contact_name", "email", "phone", "tax_id", "commercial_terms")}
    for valor in raw.values():
        assert valor.startswith("enc:v1:")
    assert "anna.a@importadora.example" not in "".join(raw.values()) and "Schmidt" not in "".join(raw.values())
    completo = client.get(f"{API}/clients/{cid}", headers=admin_headers).json()
    assert completo["email"] == "anna.a@importadora.example" and completo["commercialTerms"].startswith("40 t") and completo["masked"] is False
    mascarado = next(c for c in client.get(f"{API}/clients", headers=admin_headers).json() if c["id"] == cid)
    assert mascarado["masked"] and mascarado["email"] == "an•••@importadora.example" and "Schmidt" not in (mascarado["contactName"] or "")
    with SessionLocal() as db:
        consultas = db.query(AuditLog).filter(AuditLog.action == "dados_sensiveis_consultados", AuditLog.target == f"cliente {cid}").count()
    assert consultas >= 1


def test_cifra_autenticada_com_contexto_e_nonce_aleatorio():
    a, b = encrypt("segredo comercial", "teste:1"), encrypt("segredo comercial", "teste:1")
    assert a != b and decrypt(a, "teste:1") == "segredo comercial"  # nonce aleatório
    with pytest.raises(DecryptionError):
        decrypt(a, "teste:2")  # outro contexto (campo ou organização)
    adulterado = a[:-2] + ("A" if a[-2] != "A" else "B") + a[-1]
    with pytest.raises(DecryptionError):
        decrypt(adulterado, "teste:1")
    assert context(1, "email") != context(2, "email")


def test_troca_de_chave_mantem_dados_antigos_legiveis(monkeypatch):
    antigo = encrypt("dado antigo", "teste:rotacao")
    nova = base64.b64encode(os.urandom(32)).decode()
    settings = get_settings()
    monkeypatch.setattr(settings, "data_encryption_old_keys", base64.b64encode(crypto._keys()[0]).decode())
    monkeypatch.setattr(settings, "data_encryption_key", nova)
    crypto.reset_keys()
    try:
        assert decrypt(antigo, "teste:rotacao") == "dado antigo"
        assert encrypt("novo", "x").split(":")[2] != antigo.split(":")[2]  # novos dados com a chave nova
    finally:
        monkeypatch.undo()
        crypto.reset_keys()


# ------------------------------------------------------------------ 6. segredos fora do código
def test_producao_exige_segredos_reais():
    assert key_problem("") and key_problem(base64.b64encode(crypto.DEV_KEY).decode()) and key_problem("curta")
    assert key_problem(base64.b64encode(os.urandom(32)).decode()) is None


def test_nenhum_arquivo_de_segredos_versionado():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not tracked:
        pytest.skip("fora de um repositório git")
    assert not [f for f in tracked if Path(f).name == ".env" or f.endswith((".pem", ".key"))]
    assert ".env" in (ROOT / ".gitignore").read_text().split()
    example = (ROOT / ".env.example").read_text()
    for name in ("JWT_SECRET", "DATA_ENCRYPTION_KEY", "POSTGRES_PASSWORD", "S3_SECRET_ACCESS_KEY"):
        assert f"{name}=\n" in example  # modelo sem valores reais


# ------------------------------------------------------------------ 7. migração de bancos antigos
def test_banco_antigo_recebe_organizacoes_e_perfil_cliente(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/antigo.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(160), role VARCHAR(20) NOT NULL, "
                          "CONSTRAINT ck_users_role CHECK (role IN ('admin','gestor','operador')))"))
        conn.execute(text("CREATE TABLE sensors (id VARCHAR(20) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO users (id, email, role) VALUES (1, 'a@b.example', 'gestor')"))
    assert {"users.organization_id", "users.client_id", "sensors.organization_id"} <= set(ensure_columns(engine))
    assert ensure_role_constraint(engine) is True and ensure_role_constraint(engine) is False
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, email, role) VALUES (2, 'c@d.example', 'cliente')"))
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO users (id, email, role) VALUES (3, 'e@f.example', 'invasor')"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT role FROM users WHERE id = 1")).scalar() == "gestor"  # dados preservados


def test_matriz_cobre_todos_os_perfis_e_operacoes_validas():
    for recurso, (_, perms, _) in MATRIX.items():
        assert set(perms) <= {"admin", "gestor", "operador", "cliente"}
        assert all(set(ops) <= set("CRUD") for ops in perms.values()), recurso
    assert MATRIX["usuarios"][1] == {"admin": "CRUD"}


def test_matriz_exposta_pela_api_e_a_mesma_aplicada(client, cenario):
    data = client.get(f"{API}/auth/matrix", headers=cenario["u"]["operadorA"]).json()
    recursos = {r["id"]: r["permissions"] for r in data["resources"]}
    assert recursos == {k: v[1] for k, v in MATRIX.items()} and [r["id"] for r in data["roles"]] == ["admin", "gestor", "operador", "cliente"]


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
