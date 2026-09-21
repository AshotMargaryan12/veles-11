"""Уровень 1: дневной батч авто-статусов сделок (раздел 4.3 ТЗ).

По каждому чату с новой активностью:
  контекст (прошлый статус + сообщения) -> промпт П1 -> JSON ->
  примечание в сделку + задача менеджеру, если следующего шага нет или он просрочен.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from .amo import AmoClient, tomorrow_noon
from .clock import day_str, now_local, tzinfo
from .config import MAX_DIALOG_MESSAGES, Settings
from .db import Database
from .llm import BudgetExceeded, LLMError
from .matching import Match, partner_name, resolve_lead
from .models import StatusAnalysis
from .prompts import Prompts, format_dialog, format_prev_summary

log = logging.getLogger(__name__)

TASK_TEXT = "Определить следующий шаг по {partner}"


@dataclass
class ChatStatus:
    """Результат по одному чату — из него собирается дайджест."""

    chat_id: str
    partner: str
    lead_id: Optional[int]
    analysis: StatusAnalysis
    note_created: bool = False
    task_created: bool = False


@dataclass
class StatusBatchResult:
    """Итоги батча уровня 1."""

    chats_seen: int = 0
    analyzed: int = 0
    notes: int = 0
    tasks: int = 0
    unmatched: int = 0
    failed: int = 0
    budget_stopped: bool = False
    statuses: list[ChatStatus] = field(default_factory=list)

    @property
    def hot(self) -> list[ChatStatus]:
        return [item for item in self.statuses if item.analysis.is_hot]

    def stalled(self, today: Optional[date] = None) -> list[ChatStatus]:
        """Зависшие: нет next_step_date или он в прошлом."""
        return [
            item
            for item in self.statuses
            if item.analysis.next_step_overdue(
                None if today is None else _as_datetime(today)
            )
        ]


def _as_datetime(day: date) -> Any:
    from datetime import datetime, time

    return datetime.combine(day, time.min)


def build_note(analysis: StatusAnalysis, day: date) -> str:
    """Примечание в amo по формату из раздела 5 ТЗ."""
    agreed = "; ".join(analysis.agreed) if analysis.agreed else "-"
    next_step = analysis.next_step or "-"
    if analysis.next_step_date:
        next_step = f"{next_step} (до {analysis.next_step_date})"
    return "\n".join(
        [
            f"🤖 Статус на {day.strftime('%d.%m.%Y')}",
            f"Этап: {analysis.stage or '-'} | Температура: {analysis.temperature or '-'}",
            analysis.summary or "-",
            f"Зафиксировано: {agreed}",
            f"Ждём: {analysis.waiting_for or '-'}",
            f"След. шаг: {next_step}",
            f"Риск: {analysis.risk or '-'}",
        ]
    )


def analyze_chat(
    settings: Settings,
    db: Database,
    llm: Any,
    prompts: Prompts,
    chat: Any,
    day: str,
) -> Optional[tuple[StatusAnalysis, str]]:
    """Собирает контекст и получает статус сделки от LLM. None — анализировать нечего."""
    chat_id = str(chat["chat_id"])
    rows = db.messages_for_chat(chat_id, since=chat["last_analyzed_ts"], limit=MAX_DIALOG_MESSAGES)
    if not rows:
        return None

    dialog = format_dialog(rows)
    prev = format_prev_summary(db.last_analysis(chat_id))
    data = llm.complete_json(prompts.status_system(), prompts.status_user(prev, dialog), day)
    last_ts = str(rows[-1]["ts"])
    return StatusAnalysis.from_json(data), last_ts


def run_status_batch(
    settings: Settings,
    db: Database,
    llm: Any,
    prompts: Optional[Prompts] = None,
    amo: Optional[AmoClient] = None,
    since: Optional[str] = None,
    dry_run: bool = False,
) -> StatusBatchResult:
    """Прогон уровня 1 по всем чатам с новой активностью."""
    prompts = prompts or Prompts.load(settings.prompts_dir)
    local_now = now_local(settings)
    today = local_now.date()
    day = day_str(settings, today)
    result = StatusBatchResult()

    chats = db.chats_with_activity(since=since)
    result.chats_seen = len(chats)
    log.info("Уровень 1: чатов с активностью — %d", len(chats))

    for chat in chats:
        chat_id = str(chat["chat_id"])
        partner = partner_name(chat)
        try:
            analyzed = analyze_chat(settings, db, llm, prompts, chat, day)
        except BudgetExceeded as exc:
            log.warning("Уровень 1 остановлен: %s", exc)
            db.record_failure(day, "status_budget", None, str(exc))
            result.budget_stopped = True
            break
        except LLMError as exc:
            log.warning("Чат %s: LLM не ответила (%s)", chat_id, exc)
            db.record_failure(day, "status_llm", chat_id, str(exc))
            result.failed += 1
            continue

        if analyzed is None:
            continue
        analysis, last_ts = analyzed
        result.analyzed += 1

        match = Match()
        if amo is not None and not dry_run:
            try:
                match = resolve_lead(db, amo, chat, settings)
            except Exception as exc:  # noqa: BLE001 — сбой amo не отменяет анализ
                log.warning("Чат %s: матчинг не удался (%s)", chat_id, exc)
                db.record_failure(day, "status_match", chat_id, str(exc))
                result.failed += 1
        elif chat["amo_lead_id"]:
            match = Match(lead_id=int(chat["amo_lead_id"]), source="cache")

        status = ChatStatus(
            chat_id=chat_id,
            partner=analysis.partner or partner,
            lead_id=match.lead_id,
            analysis=analysis,
        )

        if not match.matched:
            result.unmatched += 1
            log.info("Чат %s без сделки в amo (%s)", chat_id, match.reason or "нет данных")
        elif amo is not None and not dry_run:
            status.note_created, status.task_created = _write_to_amo(
                settings, db, amo, match.lead_id, status, today, day
            )
            result.notes += int(status.note_created)
            result.tasks += int(status.task_created)

        db.save_analysis(chat_id, match.lead_id, analysis.to_dict())
        db.mark_analyzed(chat_id, last_ts)
        result.statuses.append(status)

    log.info(
        "Уровень 1: проанализировано %d, примечаний %d, задач %d, без сделки %d, ошибок %d",
        result.analyzed, result.notes, result.tasks, result.unmatched, result.failed,
    )
    return result


def _write_to_amo(
    settings: Settings,
    db: Database,
    amo: AmoClient,
    lead_id: int,
    status: ChatStatus,
    today: date,
    day: str,
) -> tuple[bool, bool]:
    """Примечание в сделку и, при необходимости, задача менеджеру."""
    note_ok = task_ok = False
    try:
        amo.add_note(lead_id, build_note(status.analysis, today))
        note_ok = True
    except Exception as exc:  # noqa: BLE001 — сбой одной сделки не валит батч
        log.warning("Сделка %s: примечание не записано (%s)", lead_id, exc)
        db.record_failure(day, "status_note", status.chat_id, str(exc))

    if status.analysis.next_step_overdue(_as_datetime(today)):
        try:
            amo.create_task(
                lead_id=lead_id,
                text=TASK_TEXT.format(partner=status.partner),
                complete_till=tomorrow_noon(tzinfo(settings)),
                responsible_user_id=settings.amo_manager_user_id,
            )
            task_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Сделка %s: задача не создана (%s)", lead_id, exc)
            db.record_failure(day, "status_task", status.chat_id, str(exc))

    return note_ok, task_ok
