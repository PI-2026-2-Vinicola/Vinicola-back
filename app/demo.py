"""
Dados de DEMONSTRAÇÃO — somente sob comando explícito (`python -m app.cli seed-demo`).

Cria três sensores com código DEMO-xx, sem coordenadas (nenhuma localização inventada),
e leituras sintéticas com origem "demonstracao", que a interface identifica com um selo.
Tudo é removido com `python -m app.cli clear-demo`. Use apenas para apresentar a
interface antes de existir captura real; não misture com dados de produção.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from .catalog import MATURATIONS
from .models import EnvironmentReading, Reading, Sensor, Variety
from .services.classifier import analyze
from .services.detector import MockDetector
from .services.readings import persist_reading
from .services.timeutil import farm_tz, utcnow

DEMO_PREFIX = "DEMO-"
DEMO_SENSORS = [
    ("DEMO-01", "Sensor de demonstração 01", "cabernet-sauvignon", 0.74, 0.09),
    ("DEMO-02", "Sensor de demonstração 02", "syrah", 0.70, 0.10),
    ("DEMO-03", "Sensor de demonstração 03", "chenin-blanc", 0.68, 0.12),
]


def _quality(rng: random.Random, p_boa: float, p_crit: float) -> str:
    r = rng.random()
    if r < p_boa:
        return "boa"
    return "atencao" if r < 1 - p_crit else "critica"


def seed_demo(db: Session, days: int = 30, interval_min: int = 120) -> int:
    """Cria os sensores DEMO-xx e o histórico sintético. Retorna o número de leituras criadas."""
    if db.query(Sensor.id).filter(Sensor.id.like(f"{DEMO_PREFIX}%")).first():
        raise RuntimeError("Os dados de demonstração já existem. Rode `clear-demo` antes de gerar novamente.")
    if not db.query(Variety.id).first():
        raise RuntimeError("Catálogo de variedades vazio. Inicie a API uma vez antes de gerar a demonstração.")
    tz = farm_tz()
    now = utcnow()
    today = now.astimezone(tz).date()
    rng = random.Random(2026)
    mock = MockDetector()
    created = 0
    for sid, name, variety, p_boa, p_crit in DEMO_SENSORS:
        sensor = Sensor(
            id=sid, name=name, block="Demonstração", location="Dados sintéticos — sem localização real",
            variety_id=variety, device="Demonstração", capture_interval_min=interval_min, active=True,
        )
        db.add(sensor)
        db.flush()
        for d in range(days - 1, -1, -1):
            day = today - timedelta(days=d)
            progress = (days - 1 - d) / max(1, days - 1)
            minute = 6 * 60
            while minute <= 18 * 60:
                at = datetime.combine(day, time(0, 0), tz) + timedelta(minutes=minute + rng.randint(-5, 5))
                minute += interval_min
                if at > now:
                    continue
                quality = _quality(rng, p_boa, p_crit)
                out = mock.simulate(rng, variety, quality)
                analysis = analyze(out.detections, variety, seed=rng.randint(0, 10**9))
                stage = 1.2 + progress * 2.4 + rng.uniform(-0.4, 0.4) - (0.6 if quality != "boa" and rng.random() < 0.4 else 0)
                analysis.maturation = MATURATIONS[max(0, min(4, int(stage)))]
                persist_reading(
                    db, sensor=sensor, analysis=analysis, detections=out.detections, captured_at=at,
                    model_version=out.model_version, processing_ms=0, source="demonstracao", commit=False,
                )
                db.add(
                    EnvironmentReading(
                        sensor_id=sid, measured_at=at.replace(microsecond=0), source="demonstracao",
                        temperature_c=round(24 + 7 * (1 - abs(minute / 60 - 14) / 8) + rng.uniform(-1.5, 1.5), 1),
                        humidity_pct=round(min(95, max(25, 62 - 18 * (1 - abs(minute / 60 - 14) / 8) + rng.uniform(-5, 5))), 1),
                    )
                )
                created += 1
    db.commit()
    return created


def clear_demo(db: Session) -> tuple[int, int]:
    """Remove leituras de demonstração e os sensores DEMO-xx. Retorna (leituras, sensores)."""
    readings = db.query(Reading).filter(Reading.source == "demonstracao").all()
    for r in readings:
        db.delete(r)
    db.query(EnvironmentReading).filter(EnvironmentReading.source == "demonstracao").delete(synchronize_session=False)
    sensors = db.query(Sensor).filter(Sensor.id.like(f"{DEMO_PREFIX}%")).all()
    for s in sensors:
        if db.query(Reading.id).filter(Reading.sensor_id == s.id, Reading.source != "demonstracao").first():
            continue  # sensor reaproveitado com dados reais: não remove
        db.delete(s)
    db.commit()
    return len(readings), len(sensors)
