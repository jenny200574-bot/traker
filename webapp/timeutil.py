"""Fechas con zona explícita. America/Bogota es el valor inicial, no una suposición oculta."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("Se requiere una fecha con zona horaria.")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"Fecha sin zona: {value}")
    return dt.astimezone(timezone.utc)


def zone_or_default(name: str, default: str = "America/Bogota") -> ZoneInfo:
    for candidate in (name, default, "UTC"):
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return ZoneInfo("UTC")


def assume_zone(naive: datetime, zone_name: str) -> datetime:
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return naive.replace(tzinfo=zone_or_default(zone_name)).astimezone(timezone.utc)


def format_local(value: str | None, zone_name: str) -> str:
    if not value:
        return "—"
    try:
        dt = parse_iso(value)
    except ValueError:
        return value
    return dt.astimezone(zone_or_default(zone_name)).strftime("%Y-%m-%d %H:%M")


def window_bounds(hours: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    end = now or utcnow()
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(hours=hours)
    return start, end
