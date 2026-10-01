"""Status calculado dos sensores e serialização com estatísticas agregadas (sem N+1)."""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Reading, Sensor
from ..schemas import SensorOut
from .timeutil import from_db, utcnow

LOW_BATTERY = 25
WEAK_SIGNAL = -80


def compute_status(sensor: Sensor, now: datetime | None = None) -> tuple[str, str]:
    """Retorna (status, motivo) a partir de dados reais: última comunicação, bateria e sinal."""
    if not sensor.active:
        return "inativo", "Sensor desativado pelo administrador"
    last = from_db(sensor.last_communication)
    if last is None:
        return "offline", "Ainda não houve comunicação com o dispositivo"
    now = now or utcnow()
    interval = timedelta(minutes=max(5, sensor.capture_interval_min))
    silence = now - last
    if silence > interval * get_settings().sensor_offline_factor:
        return "offline", f"Sem comunicação há {format_duration(silence)}"
    reasons = []
    if sensor.battery is not None and sensor.battery < LOW_BATTERY:
        reasons.append(f"bateria em {sensor.battery}%")
    if sensor.signal_dbm is not None and sensor.signal_dbm <= WEAK_SIGNAL:
        reasons.append(f"sinal fraco ({sensor.signal_dbm} dBm)")
    if silence > interval * 1.5:
        reasons.append(f"comunicação atrasada ({format_duration(silence)})")
    if reasons:
        return "atencao", "; ".join(reasons).capitalize()
    return "online", "Comunicação dentro do intervalo esperado"


def format_duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    return f"{hours // 24} dias"


def has_valid_location(s: Sensor) -> bool:
    return (
        s.latitude is not None
        and s.longitude is not None
        and -90 <= s.latitude <= 90
        and -180 <= s.longitude <= 180
        and not (s.latitude == 0 and s.longitude == 0)
    )


def sensor_stats(db: Session, ids: list[str] | None = None) -> dict[str, dict]:
    """Contagem de análises, última leitura e % de leituras "boa" por sensor em duas consultas agregadas."""
    q = db.query(Reading.sensor_id, func.count(Reading.id), func.max(Reading.captured_at)).group_by(Reading.sensor_id)
    if ids is not None:
        q = q.filter(Reading.sensor_id.in_(ids))
    stats = {sid: {"count": n, "last": last, "boa": 0} for sid, n, last in q.all()}
    qb = db.query(Reading.sensor_id, func.count(Reading.id)).filter(Reading.quality == "boa").group_by(Reading.sensor_id)
    if ids is not None:
        qb = qb.filter(Reading.sensor_id.in_(ids))
    for sid, n in qb.all():
        if sid in stats:
            stats[sid]["boa"] = n
    return stats


def to_sensor_out(s: Sensor, stats: dict | None = None, now: datetime | None = None) -> SensorOut:
    st = (stats or {}).get(s.id, {"count": 0, "last": None, "boa": 0})
    status, reason = compute_status(s, now)
    return SensorOut(
        id=s.id,
        name=s.name,
        block=s.block,
        location=s.location,
        lat=s.latitude,
        lng=s.longitude,
        has_valid_location=has_valid_location(s),
        variety_id=s.variety_id,
        device=s.device,
        firmware=s.firmware,
        battery=s.battery,
        signal=s.signal_dbm,
        capture_interval_min=s.capture_interval_min,
        active=s.active,
        status=status,  # type: ignore[arg-type]
        status_reason=reason,
        installed_at=s.installed_at,
        last_communication=from_db(s.last_communication),
        analyses_count=st["count"],
        last_reading_at=from_db(st["last"]),
        quality_ratio=round(st["boa"] / st["count"], 4) if st["count"] else None,
    )


def status_counts(sensors: list[Sensor], now: datetime | None = None) -> dict[str, int]:
    counts = {"online": 0, "atencao": 0, "offline": 0, "inativo": 0}
    for s in sensors:
        counts[compute_status(s, now)[0]] += 1
    return counts
