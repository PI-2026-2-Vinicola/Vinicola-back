"""Datas em UTC no banco; agrupamentos e filtros por dia no fuso da propriedade."""

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import get_settings


@lru_cache
def farm_tz() -> tzinfo:
    name = get_settings().oasis_timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # Windows sem o pacote tzdata
        return timezone(timedelta(hours=-3), name)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Datas sem fuso são interpretadas no fuso da propriedade."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=farm_tz())
    return dt.astimezone(timezone.utc)


def from_db(dt: datetime | None) -> datetime | None:
    """SQLite devolve datas sem fuso (gravadas em UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def local_day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, farm_tz()).astimezone(timezone.utc)


def period_bounds(days: int | None, date_from: date | None, date_to: date | None, now: datetime | None = None) -> tuple[datetime | None, datetime]:
    """Período em UTC. `days=N` = de 00h de N-1 dias atrás (horário local) até agora; datas são dias locais inteiros."""
    now = now or utcnow()
    start: datetime | None = None
    end = now
    if date_from:
        start = local_day_start(date_from)
    elif days:
        today = now.astimezone(farm_tz()).date()
        start = local_day_start(today - timedelta(days=days - 1))
    if date_to:
        end = min(now, local_day_start(date_to + timedelta(days=1)) - timedelta(microseconds=1))
    return start, end


def local_date(dt: datetime) -> date:
    return from_db(dt).astimezone(farm_tz()).date()


def local_hour(dt: datetime) -> int:
    return from_db(dt).astimezone(farm_tz()).hour
