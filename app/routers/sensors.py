from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import authorize_device, current_user_optional, device_token_header, reader
from ..models import Sensor, SensorTelemetry, User
from ..schemas import HeartbeatIn, SensorOut

router = APIRouter(prefix="/sensors", tags=["Sensores"])


def to_sensor_out(s: Sensor) -> SensorOut:
    return SensorOut(
        id=s.id, name=s.name, block=s.block, location=s.location, lat=s.latitude, lng=s.longitude, status=s.status,  # type: ignore[arg-type]
        variety_id=s.variety_id, device=s.device, firmware=s.firmware, battery=s.battery, signal=s.signal_dbm,
        capture_interval_min=s.capture_interval_min, installed_at=s.installed_at,
        last_communication=s.last_communication.replace(tzinfo=timezone.utc) if s.last_communication and s.last_communication.tzinfo is None else s.last_communication,
    )


def get_sensor_or_404(db: Session, sensor_id: str) -> Sensor:
    sensor = db.get(Sensor, sensor_id)
    if not sensor:
        raise HTTPException(404, f"Sensor {sensor_id} não encontrado.")
    return sensor


def refresh_status(sensor: Sensor) -> None:
    """Atualiza o status a partir da bateria e do sinal informados pelo dispositivo."""
    sensor.status = "atencao" if sensor.battery < 25 or sensor.signal_dbm <= -80 else "online"


@router.get("", response_model=list[SensorOut], response_model_by_alias=True)
def list_sensors(db: Session = Depends(get_db), _: User | None = Depends(reader)):
    return [to_sensor_out(s) for s in db.query(Sensor).order_by(Sensor.id).all()]


@router.get("/{sensor_id}", response_model=SensorOut, response_model_by_alias=True)
def get_sensor(sensor_id: str, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    return to_sensor_out(get_sensor_or_404(db, sensor_id))


@router.post("/{sensor_id}/heartbeat", response_model=SensorOut, response_model_by_alias=True)
def heartbeat(
    sensor_id: str,
    body: HeartbeatIn,
    db: Session = Depends(get_db),
    token: str | None = Depends(device_token_header),
    user: User | None = Depends(current_user_optional),
):
    """Sinal de vida do dispositivo (bateria, sinal Wi-Fi e firmware)."""
    sensor = get_sensor_or_404(db, sensor_id)
    authorize_device(sensor, token, user)
    if body.battery is not None:
        sensor.battery = body.battery
    if body.signal is not None:
        sensor.signal_dbm = body.signal
    if body.firmware:
        sensor.firmware = body.firmware
    sensor.last_communication = datetime.now(timezone.utc)
    refresh_status(sensor)
    db.add(SensorTelemetry(sensor_id=sensor.id, battery=body.battery, signal_dbm=body.signal, firmware=body.firmware))
    db.commit()
    return to_sensor_out(sensor)
