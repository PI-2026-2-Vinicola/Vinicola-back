from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import authorize_device, client_ip, current_user_optional, device_token_header, reader, require_roles
from ..models import EnvironmentReading, Sensor, SensorTelemetry, User, Variety
from ..schemas import EnvironmentIn, EnvironmentPoint, HeartbeatIn, SensorCreateIn, SensorOut, SensorTokenOut, SensorUpdateIn, TelemetryPoint
from ..security import hash_token, new_device_token
from ..services.audit import audit
from ..services.sensors import sensor_stats, to_sensor_out
from ..services.timeutil import as_utc, from_db, period_bounds, utcnow

router = APIRouter(prefix="/sensors", tags=["Sensores"])
admin_only = require_roles("admin")


def get_sensor_or_404(db: Session, sensor_id: str) -> Sensor:
    sensor = db.get(Sensor, sensor_id.upper())
    if not sensor:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sensor {sensor_id} não encontrado.")
    return sensor


def _check_variety(db: Session, variety_id: str) -> None:
    if not db.get(Variety, variety_id):
        raise HTTPException(422, f"Variedade “{variety_id}” não cadastrada.")


@router.get("", response_model=list[SensorOut], response_model_by_alias=True)
def list_sensors(db: Session = Depends(get_db), _: User | None = Depends(reader)):
    sensors = db.query(Sensor).order_by(Sensor.id).all()
    stats = sensor_stats(db)
    now = utcnow()
    return [to_sensor_out(s, stats, now) for s in sensors]


@router.get("/{sensor_id}", response_model=SensorOut, response_model_by_alias=True)
def get_sensor(sensor_id: str, db: Session = Depends(get_db), _: User | None = Depends(reader)):
    sensor = get_sensor_or_404(db, sensor_id)
    return to_sensor_out(sensor, sensor_stats(db, [sensor.id]))


@router.post("", response_model=SensorTokenOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
def create_sensor(body: SensorCreateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    if db.get(Sensor, body.id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Já existe um sensor com o código {body.id}.")
    _check_variety(db, body.variety_id)
    token = new_device_token()
    data = body.model_dump()
    sensor = Sensor(
        id=data["id"], name=data["name"].strip(), block=data["block"].strip(), location=data["location"].strip(),
        latitude=data["lat"], longitude=data["lng"], variety_id=data["variety_id"], device=data["device"], firmware=data["firmware"],
        capture_interval_min=data["capture_interval_min"], installed_at=data["installed_at"], active=True, device_token_hash=hash_token(token),
    )
    db.add(sensor)
    audit(db, "sensor_criado", actor=admin.email, target=sensor.id, details={"nome": sensor.name, "bloco": sensor.block}, ip=client_ip(request))
    return SensorTokenOut(sensor=to_sensor_out(sensor, {}), device_token=token)


@router.patch("/{sensor_id}", response_model=SensorOut, response_model_by_alias=True)
def update_sensor(sensor_id: str, body: SensorUpdateIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    sensor = get_sensor_or_404(db, sensor_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("variety_id"):
        _check_variety(db, changes["variety_id"])
    if changes.pop("clear_location", False):
        sensor.latitude = sensor.longitude = None
    lat, lng = changes.pop("lat", None), changes.pop("lng", None)
    if (lat is None) != (lng is None):
        raise HTTPException(422, "Informe latitude e longitude juntas.")
    if lat is not None:
        if lat == 0 and lng == 0:
            raise HTTPException(422, "Coordenadas 0,0 não são válidas.")
        sensor.latitude, sensor.longitude = lat, lng
    for key, value in changes.items():
        if value is not None:
            setattr(sensor, key, value.strip() if isinstance(value, str) else value)
    audit(db, "sensor_alterado", actor=admin.email, target=sensor.id, details=body.model_dump(exclude_unset=True), ip=client_ip(request))
    return to_sensor_out(sensor, sensor_stats(db, [sensor.id]))


@router.post("/{sensor_id}/token", response_model=SensorTokenOut, response_model_by_alias=True)
def regenerate_token(sensor_id: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    """Gera um novo token para o dispositivo. O anterior deixa de funcionar imediatamente."""
    sensor = get_sensor_or_404(db, sensor_id)
    token = new_device_token()
    sensor.device_token_hash = hash_token(token)
    audit(db, "token_sensor_regenerado", actor=admin.email, target=sensor.id, ip=client_ip(request))
    return SensorTokenOut(sensor=to_sensor_out(sensor, sensor_stats(db, [sensor.id])), device_token=token)


def _record_environment(db: Session, sensor: Sensor, at: datetime, values: dict, source: str) -> bool:
    if all(values.get(k) is None for k in ("temperature_c", "humidity_pct", "luminosity_lux", "soil_moisture_pct")):
        return False
    exists = db.query(EnvironmentReading.id).filter(EnvironmentReading.sensor_id == sensor.id, EnvironmentReading.measured_at == at).first()
    if exists:
        return False
    db.add(EnvironmentReading(sensor_id=sensor.id, measured_at=at, source=source, **{k: values.get(k) for k in ("temperature_c", "humidity_pct", "luminosity_lux", "soil_moisture_pct")}))
    return True


@router.post("/{sensor_id}/heartbeat", response_model=SensorOut, response_model_by_alias=True)
def heartbeat(
    sensor_id: str,
    body: HeartbeatIn,
    db: Session = Depends(get_db),
    token: str | None = Depends(device_token_header),
    user: User | None = Depends(current_user_optional),
):
    """Sinal de vida do dispositivo: bateria, sinal Wi-Fi, firmware e (opcional) temperatura e umidade."""
    sensor = get_sensor_or_404(db, sensor_id)
    authorize_device(sensor, token, user)
    now = utcnow()
    apply_device_status(db, sensor, body.battery, body.signal, body.firmware, now)
    _record_environment(db, sensor, now, {"temperature_c": body.temperature_c, "humidity_pct": body.humidity_pct}, "sensor")
    db.commit()
    return to_sensor_out(sensor, sensor_stats(db, [sensor.id]), now)


def apply_device_status(db: Session, sensor: Sensor, battery: int | None, signal: int | None, firmware: str | None, now: datetime) -> None:
    sensor.last_communication = now
    if battery is not None:
        sensor.battery = battery
    if signal is not None:
        sensor.signal_dbm = signal
    if firmware:
        sensor.firmware = firmware
    if battery is not None or signal is not None or firmware:
        db.add(SensorTelemetry(sensor_id=sensor.id, received_at=now, battery=battery, signal_dbm=signal, firmware=firmware))


@router.post("/{sensor_id}/environment", status_code=status.HTTP_201_CREATED)
def post_environment(
    sensor_id: str,
    body: EnvironmentIn,
    db: Session = Depends(get_db),
    token: str | None = Depends(device_token_header),
    user: User | None = Depends(current_user_optional),
):
    sensor = get_sensor_or_404(db, sensor_id)
    authorize_device(sensor, token, user)
    at = as_utc(body.measured_at) if body.measured_at else utcnow()
    if at > utcnow():
        raise HTTPException(422, "Data da medição no futuro.")
    created = _record_environment(db, sensor, at, body.model_dump(), "sensor" if token else "manual")
    if not created:
        raise HTTPException(status.HTTP_409_CONFLICT, "Medição vazia ou já registrada para este horário.")
    if token:
        sensor.last_communication = utcnow()
    db.commit()
    return {"status": "ok"}


@router.get("/{sensor_id}/environment", response_model=list[EnvironmentPoint], response_model_by_alias=True)
def list_environment(
    sensor_id: str,
    days: int | None = Query(default=7, ge=1, le=366),
    date_from: date | None = Query(default=None, alias="dateFrom"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    db: Session = Depends(get_db),
    _: User | None = Depends(reader),
):
    sensor = get_sensor_or_404(db, sensor_id)
    start, end = period_bounds(days, date_from, date_to)
    q = db.query(EnvironmentReading).filter(EnvironmentReading.sensor_id == sensor.id, EnvironmentReading.measured_at <= end)
    if start:
        q = q.filter(EnvironmentReading.measured_at >= start)
    rows = q.order_by(EnvironmentReading.measured_at).limit(5000).all()
    return [
        EnvironmentPoint(measured_at=from_db(r.measured_at), temperature_c=r.temperature_c, humidity_pct=r.humidity_pct, luminosity_lux=r.luminosity_lux, soil_moisture_pct=r.soil_moisture_pct, source=r.source)
        for r in rows
    ]


@router.get("/{sensor_id}/telemetry", response_model=list[TelemetryPoint], response_model_by_alias=True)
def list_telemetry(sensor_id: str, days: int = Query(default=7, ge=1, le=366), db: Session = Depends(get_db), _: User | None = Depends(reader)):
    sensor = get_sensor_or_404(db, sensor_id)
    start, _end = period_bounds(days, None, None)
    rows = (
        db.query(SensorTelemetry)
        .filter(SensorTelemetry.sensor_id == sensor.id, SensorTelemetry.received_at >= start)
        .order_by(SensorTelemetry.received_at)
        .limit(5000)
        .all()
    )
    return [TelemetryPoint(received_at=from_db(r.received_at), battery=r.battery, signal=r.signal_dbm, firmware=r.firmware) for r in rows]
