"""
Dados de demonstração: propriedade em Lagoa Grande (PE), 6 sensores e 30 dias de leituras.
Mesma lógica do gerador do frontend — a demonstração é equivalente nos dois modos.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .catalog import MATURATIONS, VARIETIES
from .models import Sensor, User, Variety
from .security import hash_secret
from .services.classifier import analyze
from .services.detector import MockDetector
from .services.readings import persist_reading

FARM_TZ = ZoneInfo("America/Recife")
HISTORY_DAYS = 30
DEMO_PASSWORD = "osais2026"

DEMO_USERS = [
    ("Ana Ribeiro", "admin@osais.agr.br", "admin"),
    ("Carlos Menezes", "gestor@osais.agr.br", "gestor"),
    ("Júlia Santos", "operador@osais.agr.br", "operador"),
]

SENSORS = [
    # id, nome, bloco, local, lat, lng, status, variedade, dispositivo, firmware, bateria, sinal, intervalo, instalação, horas sem comunicação
    ("S-001", "OSAIS Cam 01", "Bloco A", "Bloco A — Fileira 12", -8.99375, -40.27552, "online", "cabernet-sauvignon", "ESP32-CAM (OV2640)", "v1.4.2", 92, -58, 90, "2026-03-02", 0),
    ("S-002", "OSAIS Cam 02", "Bloco B", "Bloco B — Fileira 07", -8.99390, -40.27158, "online", "syrah", "ESP32-CAM (OV2640)", "v1.4.2", 87, -61, 90, "2026-03-02", 0),
    ("S-003", "OSAIS Cam 03", "Bloco C", "Bloco C — Fileira 21", -8.99360, -40.26770, "online", "chenin-blanc", "ESP32-S3 + OV5640", "v1.5.0", 78, -66, 75, "2026-04-11", 0),
    ("S-004", "OSAIS Cam 04", "Bloco D", "Bloco D — Fileira 03", -8.99690, -40.27560, "online", "tempranillo", "ESP32-CAM (OV2640)", "v1.4.2", 81, -63, 90, "2026-04-11", 0),
    ("S-005", "OSAIS Cam 05", "Bloco E", "Bloco E — Fileira 15", -8.99705, -40.27170, "atencao", "moscato-canelli", "ESP32-CAM (OV2640)", "v1.3.9", 23, -79, 120, "2026-05-20", 3),
    ("S-006", "OSAIS Cam 06", "Bloco F", "Bloco F — Fileira 09", -8.99680, -40.26780, "offline", "touriga-nacional", "ESP32-S3 + OV5640", "v1.5.0", 0, -95, 90, "2026-05-20", 44),
]

QUALITY_PROFILE = {
    "cabernet-sauvignon": (0.74, 0.09, -0.25),
    "syrah": (0.72, 0.08, 0.0),
    "tempranillo": (0.71, 0.10, 0.30),
    "touriga-nacional": (0.77, 0.07, 0.0),
    "chenin-blanc": (0.70, 0.09, -0.10),
    "moscato-canelli": (0.73, 0.08, 0.35),
}


def device_token_for(sensor_id: str) -> str:
    """Token de demonstração do dispositivo. Em produção, gere tokens aleatórios e grave-os no firmware."""
    return f"osais-dev-{sensor_id.lower()}"


def _quality(rng: random.Random, variety: str, day_index: int) -> str:
    p_boa, p_crit, _ = QUALITY_PROFILE[variety]
    if HISTORY_DAYS - 13 <= day_index <= HISTORY_DAYS - 10:  # evento de chuva
        p_boa -= 0.14
        p_crit += 0.04
    if variety == "chenin-blanc" and day_index >= HISTORY_DAYS - 8:
        p_boa -= 0.20
        p_crit += 0.11
    if variety == "syrah":
        p_boa += (day_index / HISTORY_DAYS) * 0.12
    r = rng.random()
    if r < p_boa:
        return "boa"
    return "atencao" if r < 1 - p_crit else "critica"


def _maturation(rng: random.Random, variety: str, day_index: int, quality: str) -> str:
    stage = 1.25 + (day_index / HISTORY_DAYS) * 2.25 + QUALITY_PROFILE[variety][2] + rng.uniform(-0.35, 0.35)
    if quality != "boa" and rng.random() < 0.4:
        stage -= 0.7
    return MATURATIONS[max(0, min(4, int(stage)))]


def seed_catalog(db: Session) -> None:
    if not db.query(Variety).first():
        db.add_all(Variety(**v) for v in VARIETIES)
    if not db.query(User).first():
        db.add_all(User(name=n, email=e, role=r, password_hash=hash_secret(DEMO_PASSWORD)) for n, e, r in DEMO_USERS)
    db.commit()


def seed_demo(db: Session, now: datetime | None = None) -> int:
    """Popula sensores e histórico. Retorna o número de leituras criadas."""
    seed_catalog(db)
    if db.query(Sensor).first():
        return 0
    now = (now or datetime.now(timezone.utc)).astimezone(FARM_TZ)
    sensors: dict[str, Sensor] = {}
    for (sid, name, block, loc, lat, lng, status, variety, device, fw, bat, sig, interval, installed, silent) in SENSORS:
        heartbeat = timedelta(hours=silent) if silent else timedelta(minutes=random.Random(sid).randint(1, 4))
        sensors[sid] = Sensor(
            id=sid, name=name, block=block, location=loc, latitude=lat, longitude=lng, status=status, variety_id=variety,
            device=device, firmware=fw, battery=bat, signal_dbm=sig, capture_interval_min=interval,
            installed_at=date.fromisoformat(installed), last_communication=now - heartbeat,
            device_token_hash=hash_secret(device_token_for(sid)),
        )
    db.add_all(sensors.values())
    db.flush()

    rng = random.Random(20260923)
    mock = MockDetector()
    schedule: list[tuple[datetime, str, int]] = []
    today = now.date()
    for (sid, *_rest) in SENSORS:
        status, interval, silent = _rest[5], _rest[11], _rest[13]
        srng = random.Random(int(sid[2:]) * 7919)
        silent_since = now - timedelta(hours=silent)
        for d in range(HISTORY_DAYS - 1, -1, -1):
            day_index = HISTORY_DAYS - 1 - d
            day = today - timedelta(days=d)
            minutes = 6 * 60 + srng.randint(0, 25)
            while minutes <= 18 * 60:
                at = datetime.combine(day, time(0, 0), FARM_TZ) + timedelta(minutes=minutes + srng.randint(-8, 8))
                minutes += interval
                if at > silent_since:
                    continue
                if status == "atencao" and d <= 3 and srng.random() < 0.45:
                    continue
                schedule.append((at, sid, day_index))
    schedule.sort()

    for at, sid, day_index in schedule:
        sensor = sensors[sid]
        quality = _quality(rng, sensor.variety_id, day_index)
        out = mock.simulate(rng, sensor.variety_id, quality)
        analysis = analyze(out.detections, sensor.variety_id, seed=rng.randint(0, 10**9))
        analysis.maturation = _maturation(rng, sensor.variety_id, day_index, quality)
        persist_reading(
            db, sensor=sensor, analysis=analysis, detections=out.detections, captured_at=at,
            model_version="YOLOv8n-osais v0.3", processing_ms=rng.randint(180, 640),
            image_seed=rng.randint(0, 10**9), commit=False,
        )
    db.commit()
    return len(schedule)
