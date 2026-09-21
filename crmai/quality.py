"""Уровень 2: контроль качества исходящих (раздел 4.4 ТЗ).

Берём все исходящие менеджера за день по каждому чату, добавляем немного
контекста и отдаём LLM по промпту П2. Критичные замечания, которые не поймал
regex-слой, уходят отдельным алертом сразу после батча.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from .amo import AmoClient
from .clock import day_bounds, day_str, today as today_of
from .config import QUALITY_CONTEXT_MESSAGES, Settings
from .db import Database
from .llm import BudgetExceeded, LLMError
from .matching import partner_name, resolve_lead
from .models import QualityVerdict
from .prompts import Prompts, format_quality_block
from .redflags import normalize, scan_text
from .telegram import TelegramNotifier, format_alert

log = logging.getLogger(__name__)

# Сколько диалогов отдаём модели за один запрос: держим промпт в разумном размере
CHATS_PER_CALL = 10


@dataclass
class QualityBatchResult:
    """Итоги батча уровня 2."""

    chats_checked: int = 0
    ok_chats: int = 0
    failed: int = 0
    budget_stopped: bool = False
    day_note: str = ""
    verdicts: list[QualityVerdict] = field(default_factory=list)
    alerted: list[QualityVerdict] = field(default_factory=list)

    @property
    def critical(self) -> list[QualityVerdict]:
        return [v for v in self.verdicts if v.is_critical]


@dataclass
class ChatBlock:
    """Один диалог, подготовленный для промпта П2."""

    chat_id: str
    partner: str
    lead_id: Optional[int]
    text: str
    outgoing_texts: list[str] = field(default_factory=list)


def collect_blocks(
    settings: Settings,
    db: Database,
    day: date,
    amo: Optional[AmoClient] = None,
) -> list[ChatBlock]:
    """Готовит по диалогу на каждый чат, где менеджер писал в этот день."""
    day_start, day_end = day_bounds(settings, day)
    blocks: list[ChatBlock] = []

    for chat in db.chats_active_between(day_start, day_end):
        chat_id = str(chat["chat_id"])
        outgoing = db.outgoing_for_day(chat_id, day_start, day_end)
        if not outgoing:
            continue
        context = db.messages_before(chat_id, str(outgoing[0]["ts"]), QUALITY_CONTEXT_MESSAGES)
        partner = partner_name(chat)
        lead_id = int(chat["amo_lead_id"]) if chat["amo_lead_id"] else None
        if lead_id is None and amo is not None:
            try:
                lead_id = resolve_lead(db, amo, chat, settings).lead_id
            except Exception as exc:  # noqa: BLE001 — без ссылки дайджест всё равно нужен
                log.warning("Чат %s: матчинг для дайджеста не удался (%s)", chat_id, exc)
        blocks.append(
            ChatBlock(
                chat_id=chat_id,
                partner=partner,
                lead_id=lead_id,
                text=format_quality_block(partner, context, outgoing),
                outgoing_texts=[str(row["text"] or "") for row in outgoing],
            )
        )
    return blocks


def attach_chats(verdicts: list[QualityVerdict], blocks: list[ChatBlock]) -> list[QualityVerdict]:
    """Привязывает замечание к чату: сначала по имени партнёра, затем по цитате."""
    by_name = {normalize(block.partner): block for block in blocks if block.partner}

    for verdict in verdicts:
        block = by_name.get(normalize(verdict.chat))
        if block is None and verdict.quote:
            quote = normalize(verdict.quote)[:60]
            for candidate in blocks:
                if any(quote and quote in normalize(text) for text in candidate.outgoing_texts):
                    block = candidate
                    break
        if block is None and len(blocks) == 1:
            block = blocks[0]
        if block is not None:
            verdict.chat_id = block.chat_id
            verdict.lead_id = block.lead_id
            if not verdict.chat:
                verdict.chat = block.partner
    return verdicts


def run_quality_batch(
    settings: Settings,
    db: Database,
    llm: Any,
    prompts: Optional[Prompts] = None,
    amo: Optional[AmoClient] = None,
    notifier: Optional[TelegramNotifier] = None,
    day: Optional[date] = None,
) -> QualityBatchResult:
    """Прогон уровня 2 за указанный день (по умолчанию — сегодня)."""
    prompts = prompts or Prompts.load(settings.prompts_dir)
    target = day or today_of(settings)
    day_key = day_str(settings, target)
    result = QualityBatchResult()

    blocks = collect_blocks(settings, db, target, amo=amo)
    result.chats_checked = len(blocks)
    log.info("Уровень 2: диалогов с исходящими — %d", len(blocks))
    if not blocks:
        return result

    notes: list[str] = []
    for start in range(0, len(blocks), CHATS_PER_CALL):
        chunk = blocks[start : start + CHATS_PER_CALL]
        user_prompt = prompts.quality_user(
            date=target.strftime("%d.%m.%Y"),
            dialogs="\n\n".join(block.text for block in chunk),
        )
        try:
            data = llm.complete_json(prompts.quality_system(), user_prompt, day_key)
        except BudgetExceeded as exc:
            log.warning("Уровень 2 остановлен: %s", exc)
            db.record_failure(day_key, "quality_budget", None, str(exc))
            result.budget_stopped = True
            break
        except LLMError as exc:
            log.warning("Уровень 2: LLM не ответила по пачке из %d чатов (%s)", len(chunk), exc)
            db.record_failure(day_key, "quality_llm", None, str(exc))
            result.failed += len(chunk)
            continue

        raw_verdicts = data.get("verdicts") or []
        parsed = [
            QualityVerdict.from_json(item) for item in raw_verdicts if isinstance(item, dict)
        ]
        result.verdicts.extend(attach_chats(parsed, chunk))
        try:
            result.ok_chats += int(data.get("ok_chats") or 0)
        except (TypeError, ValueError):
            pass
        note = str(data.get("day_note") or "").strip()
        if note:
            notes.append(note)

    result.day_note = " ".join(notes)
    db.clear_verdicts(day_key)
    db.save_verdicts(day_key, result.verdicts)
    # Дайджест собирается отдельной командой — кладём в meta то, чего нет в таблицах
    db.set_meta(f"ok_chats:{day_key}", result.ok_chats)
    db.set_meta(f"day_note:{day_key}", result.day_note)
    db.set_meta(f"quality_chats:{day_key}", result.chats_checked)

    result.alerted = alert_missed_critical(settings, result.verdicts, notifier)
    log.info(
        "Уровень 2: замечаний %d (критичных %d), без замечаний %d, ошибок %d",
        len(result.verdicts), len(result.critical), result.ok_chats, result.failed,
    )
    return result


def alert_missed_critical(
    settings: Settings,
    verdicts: list[QualityVerdict],
    notifier: Optional[TelegramNotifier],
) -> list[QualityVerdict]:
    """Критичные замечания, не пойманные regex-слоем, — отдельным алертом."""
    missed = [v for v in verdicts if v.is_critical and not scan_text(v.quote)]
    if not missed:
        return []
    log.warning("Критичных замечаний мимо regex-слоя: %d", len(missed))
    if notifier is None:
        return missed
    for verdict in missed:
        notifier.broadcast(
            format_alert(
                title=f"Критичное замечание: {verdict.issue}",
                partner=verdict.chat or "без имени",
                quote=verdict.quote,
                lead_url=settings.lead_url(verdict.lead_id) if verdict.lead_id else "",
                severity=verdict.severity,
                suggestion=verdict.suggestion,
            )
        )
    return missed
