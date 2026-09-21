"""Дневной дайджест руководителю в Telegram (раздел 6 ТЗ).

Собирается из базы, а не из результатов батчей: так команду `digest` можно
запустить повторно или за прошлый день, не перезапуская анализ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from .clock import day_bounds, day_str, today as today_of
from .config import Settings
from .db import Database
from .models import SEVERITY_ORDER, StatusAnalysis
from .telegram import escape

log = logging.getLogger(__name__)

# Сколько партнёров показываем в блоках «горячие» и «зависшие»
LIST_LIMIT = 15


@dataclass
class DigestData:
    """Цифры и списки дайджеста за день."""

    day: date
    active_chats: int = 0
    statuses_updated: int = 0
    unmatched: int = 0
    ok_chats: int = 0
    day_note: str = ""
    verdicts: list[Any] = field(default_factory=list)
    hot: list[tuple[str, str, str]] = field(default_factory=list)
    stalled: list[tuple[str, str]] = field(default_factory=list)
    failures: list[Any] = field(default_factory=list)


def collect(settings: Settings, db: Database, day: Optional[date] = None) -> DigestData:
    """Читает из базы всё, что нужно дайджесту."""
    target = day or today_of(settings)
    day_key = day_str(settings, target)
    day_start, day_end = day_bounds(settings, target)

    data = DigestData(day=target)
    data.active_chats = len(db.chats_active_between(day_start, day_end))
    data.unmatched = db.unmatched_count(day_start, day_end)
    data.verdicts = db.verdicts_for_day(day_key)
    data.failures = db.failures_for_day(day_key)
    data.day_note = db.get_meta(f"day_note:{day_key}")
    try:
        data.ok_chats = int(db.get_meta(f"ok_chats:{day_key}", "0"))
    except ValueError:
        data.ok_chats = 0

    analyses = db.analyses_since(day_start)
    data.statuses_updated = len(analyses)

    for item in analyses:
        analysis = StatusAnalysis.from_json(item["summary"])
        partner = analysis.partner or item["chat_id"]
        url = settings.lead_url(item["lead_id"]) if item["lead_id"] else ""
        if analysis.is_hot:
            step = analysis.next_step or "следующий шаг не определён"
            if analysis.next_step_date:
                step = f"{step} (до {analysis.next_step_date})"
            data.hot.append((partner, step, url))
        if analysis.next_step_overdue(_midnight(target)):
            reason = "нет даты" if not analysis.next_step_date else f"просрочен {analysis.next_step_date}"
            data.stalled.append((partner, reason))

    return data


def _midnight(day: date) -> Any:
    from datetime import datetime, time

    return datetime.combine(day, time.min)


def render(settings: Settings, data: DigestData) -> str:
    """Текст дайджеста в HTML-разметке Telegram."""
    lines = [
        f"📊 <b>Дайджест за {data.day.strftime('%d.%m.%Y')}</b>",
        f"Диалогов с активностью: {data.active_chats} | "
        f"Статусы обновлены: {data.statuses_updated} | "
        f"Без сделки в amo: {data.unmatched}",
        "",
        f"✅ Без замечаний: {data.ok_chats}",
    ]

    verdicts = sorted(
        data.verdicts,
        key=lambda row: SEVERITY_ORDER.get(_get(row, "severity"), 3),
    )
    if verdicts:
        lines.append(f"⚠️ <b>Замечания ({len(verdicts)}):</b>")
        for number, row in enumerate(verdicts, start=1):
            partner = _get(row, "partner") or "без имени"
            lines.append(
                f"{number}. [{escape(_get(row, 'severity'))}] "
                f"{escape(partner)}: {escape(_get(row, 'issue'))}"
            )
            quote = _get(row, "quote")
            if quote:
                lines.append(f"   «{escape(quote)}»")
            suggestion = _get(row, "suggestion")
            if suggestion:
                lines.append(f"   💡 {escape(suggestion)}")
            lead_id = _get(row, "lead_id")
            if lead_id:
                lines.append(f"   {settings.lead_url(lead_id)}")
    else:
        lines.append("⚠️ Замечаний нет")

    lines.append("")
    lines.append("🔥 <b>Горячие сделки:</b>")
    if data.hot:
        for partner, step, url in data.hot[:LIST_LIMIT]:
            suffix = f" — {url}" if url else ""
            lines.append(f"• {escape(partner)}: {escape(step)}{suffix}")
        if len(data.hot) > LIST_LIMIT:
            lines.append(f"• ... и ещё {len(data.hot) - LIST_LIMIT}")
    else:
        lines.append("• нет")

    lines.append("😴 <b>Зависшие (нет след. шага или просрочен):</b>")
    if data.stalled:
        for partner, reason in data.stalled[:LIST_LIMIT]:
            lines.append(f"• {escape(partner)} — {escape(reason)}")
        if len(data.stalled) > LIST_LIMIT:
            lines.append(f"• ... и ещё {len(data.stalled) - LIST_LIMIT}")
    else:
        lines.append("• нет")

    if data.failures:
        lines.append("")
        lines.append(f"❗ Не удалось обработать: {len(data.failures)} шт. — подробности в логе")

    if data.day_note:
        lines.append("")
        lines.append(escape(data.day_note))

    return "\n".join(lines)


def _get(row: Any, key: str) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = getattr(row, key, "")
    return "" if value is None else value


def build_digest(settings: Settings, db: Database, day: Optional[date] = None) -> str:
    return render(settings, collect(settings, db, day))
