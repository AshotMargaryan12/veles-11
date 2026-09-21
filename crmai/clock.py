"""Время и границы суток в часовом поясе компании (TZ из .env).

Сообщения хранятся в UTC ISO-8601, а батчи и дайджест считают «день» по
Москве. Все преобразования собраны здесь, чтобы не разъезжались.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from .config import Settings

log = logging.getLogger(__name__)


def tzinfo(settings: Settings) -> timezone:
    """Часовой пояс из настроек. Неизвестное имя — падаем в UTC с предупреждением."""
    name = settings.tz or "UTC"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001 — нет tzdata или опечатка в TZ
        log.warning("Неизвестный часовой пояс %r (%s) — работаем в UTC", name, exc)
        return timezone.utc


def now_local(settings: Settings) -> datetime:
    return datetime.now(tzinfo(settings))


def today(settings: Settings) -> date:
    return now_local(settings).date()


def day_str(settings: Settings, day: Optional[date] = None) -> str:
    """`YYYY-MM-DD` локального дня — ключ для счётчиков и таблиц за сутки."""
    return (day or today(settings)).isoformat()


def day_bounds(settings: Settings, day: Optional[date] = None) -> tuple[str, str]:
    """Границы локальных суток в виде UTC ISO — под сравнение с messages.ts."""
    tz = tzinfo(settings)
    target = day or today(settings)
    start = datetime.combine(target, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def parse_day(value: Optional[str], settings: Settings) -> date:
    """`--date 2026-09-21` -> date. Пусто — сегодня."""
    if not value:
        return today(settings)
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def is_workday(day: date) -> bool:
    """Батчи ходят по будням (раздел 4.3 ТЗ)."""
    return day.weekday() < 5
