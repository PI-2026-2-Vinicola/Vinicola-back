from app.cli import main
from app.database import SessionLocal
from app.models import Reading, Sensor

from .conftest import API


def test_demo_data_is_labeled_and_removable(client, admin_headers):
    main(["seed-demo", "--days", "2"])
    with SessionLocal() as db:
        demo = db.query(Reading).filter(Reading.source == "demonstracao").count()
        assert demo > 0
        assert all(s.latitude is None for s in db.query(Sensor).filter(Sensor.id.like("DEMO-%")))
    page = client.get(f"{API}/readings", params={"source": "demonstracao", "pageSize": 1}, headers=admin_headers).json()
    assert page["total"] == demo and page["items"][0]["modelVersion"] == "Demonstração (sintético)"
    main(["clear-demo"])
    with SessionLocal() as db:
        assert db.query(Reading).filter(Reading.source == "demonstracao").count() == 0
        assert db.query(Sensor).filter(Sensor.id.like("DEMO-%")).count() == 0


def test_create_user_command(client):
    main(["create-user", "--name", "Via CLI", "--email", "cli@teste.com", "--role", "operador", "--password", "Senha2026x"])
    res = client.post(f"{API}/auth/login", json={"email": "cli@teste.com", "password": "Senha2026x"})
    assert res.status_code == 200


def test_check_integrations_without_configuration(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["check-integrations"])
    out = capsys.readouterr().out
    assert exc.value.code == 0  # nada configurado não é falha: o comando apenas orienta
    assert "THINGSPEAK_CHANNEL_ID" in out and "EMBRAPA_CONSUMER_KEY" in out
