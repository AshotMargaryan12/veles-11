"""Демо-режим: весь конвейер на встроенных данных, без сети.

Ни Wazzup, ни amo, ни Anthropic не нужны: события берутся из фикстуры ниже,
ответы модели подменяются заглушкой, алерты и дайджест печатаются в консоль.
Нужен, чтобы потрогать формат примечаний и дайджеста до боевых ключей и чтобы
быстро проверить regex-слой на этапе Э4.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .analysis import build_note, run_status_batch
from .clock import today as today_of
from .config import Settings
from .db import Database
from .digest import build_digest
from .intake import Intake
from .llm import StubLLM
from .prompts import Prompts
from .quality import run_quality_batch

def demo_payload(day: Optional[date] = None) -> dict[str, Any]:
    """События вебхука за сегодня: демо должно показывать активность в любой день."""
    target = (day or date.today()).isoformat()

    def at(hhmm: str) -> str:
        return f"{target}T{hhmm}:00.000Z"

    batya = {"name": "Крипто Батя", "username": "@kripto_batya"}
    maria = {"name": "Трейдер Мария", "username": "@maria_trades"}

    return {
        "messages": [
            {
                "messageId": "demo-1",
                "chatId": "tg-1001",
                "chatType": "telegram",
                "status": "inbound",
                "dateTime": at("06:10"),
                "text": "Привет! Да, видел ваших ботов. Сколько платите за интеграцию?",
                "contact": batya,
            },
            {
                "messageId": "demo-2",
                "chatId": "tg-1001",
                "chatType": "telegram",
                "isEcho": True,
                "dateTime": at("06:40"),
                "text": (
                    "Привет! Смотрел твой разбор по сеточным ботам - у нас как раз конструктор "
                    "под это. Фикс 3000$ плюс рефка, детали пришлю."
                ),
                "contact": batya,
            },
            {
                "messageId": "demo-3",
                "chatId": "tg-1001",
                "chatType": "telegram",
                "isEcho": True,
                "dateTime": at("06:41"),
                "text": "И аудитория точно заработает, у нас гарантированный доход без рисков.",
                "contact": batya,
            },
            {
                "messageId": "demo-4",
                "chatId": "tg-2002",
                "chatType": "telegram",
                "status": "inbound",
                "dateTime": at("08:00"),
                "text": "Ок, гляну на днях",
                "contact": maria,
            },
            {
                "messageId": "demo-5",
                "chatId": "tg-2002",
                "chatType": "telegram",
                "isEcho": True,
                "dateTime": at("08:05"),
                "text": "Отлично, ждём!",
                "contact": maria,
            },
            {
                # Дубль: Wazzup повторяет доставку, дедуп по messageId обязан его срезать
                "messageId": "demo-5",
                "chatId": "tg-2002",
                "chatType": "telegram",
                "isEcho": True,
                "dateTime": at("08:05"),
                "text": "Отлично, ждём!",
                "contact": maria,
            },
        ]
    }


DEMO_STATUS_1 = json.dumps(
    {
        "partner": "Крипто Батя",
        "stage": "переговоры об условиях",
        "summary": "Партнёр заинтересован, спросил про оплату. Менеджер назвал сумму фикса без согласования.",
        "agreed": [],
        "waiting_for": "реакция партнёра на условия",
        "next_step": "согласовать сумму фикса с руководителем и вернуться с оффером",
        "next_step_date": None,
        "risk": "названа сумма и обещана доходность - риск обязательств",
        "temperature": "hot",
    },
    ensure_ascii=False,
)

DEMO_STATUS_2 = json.dumps(
    {
        "partner": "Трейдер Мария",
        "stage": "холодный заход",
        "summary": "Партнёр обещал посмотреть материалы, конкретной даты нет.",
        "agreed": [],
        "waiting_for": "ответ партнёра",
        "next_step": "напомнить о себе и зафиксировать дату",
        "next_step_date": "2026-09-24",
        "risk": "обещание без даты",
        "temperature": "warm",
    },
    ensure_ascii=False,
)

DEMO_QUALITY = json.dumps(
    {
        "verdicts": [
            {
                "chat": "Крипто Батя",
                "quote": "И аудитория точно заработает, у нас гарантированный доход без рисков.",
                "issue": "1. Обещание гарантированной доходности",
                "severity": "critical",
                "suggestion": "Говорить про инструмент и статистику, а не про гарантии дохода",
            },
            {
                "chat": "Трейдер Мария",
                "quote": "Отлично, ждём!",
                "issue": "9. Принято обещание партнёра без даты",
                "severity": "major",
                "suggestion": "Уточнить: «когда именно вернёшься с ответом?»",
            },
        ],
        "ok_chats": 0,
        "day_note": "День слабый: в одном диалоге прямые обещания доходности, в другом обещание без даты.",
    },
    ensure_ascii=False,
)


@dataclass
class PrintNotifier:
    """Подмена Telegram: печатает, что ушло бы руководителю."""

    settings: Settings
    sent: list[str] = field(default_factory=list)

    def broadcast(self, text: str) -> int:
        self.sent.append(text)
        print("\n--- Telegram ---")
        print(text)
        return 1

    def send(self, chat_id: str, text: str) -> bool:
        return bool(self.broadcast(text))


def run_demo(db_path: Optional[str] = None, prompts_dir: str = "prompts") -> dict[str, Any]:
    """Полный проход: приём -> алерты -> статусы -> качество -> дайджест."""
    tmp_dir: Optional[tempfile.TemporaryDirectory] = None
    if db_path is None:
        tmp_dir = tempfile.TemporaryDirectory(prefix="crmai-demo-")
        db_path = str(Path(tmp_dir.name) / "demo.db")

    settings = Settings(
        amo_domain="veles-demo",
        tz="Europe/Moscow",
        db_path=db_path,
        prompts_dir=prompts_dir,
        pipeline_ids=[1],
    )
    db = Database(db_path)
    notifier = PrintNotifier(settings)
    prompts = Prompts.load(prompts_dir)
    llm = StubLLM([DEMO_STATUS_1, DEMO_STATUS_2, DEMO_QUALITY])

    today = today_of(settings)

    print("=== 1. Приём сообщений и regex-алерты ===")
    intake = Intake(settings, db, notifier=notifier)  # type: ignore[arg-type]
    intake_result = intake.handle_payload(demo_payload(today_of(settings)))
    print(
        f"\nПолучено {intake_result.received}, сохранено {intake_result.stored}, "
        f"дублей {intake_result.duplicates}, стоп-паттернов {len(intake_result.alerts)}"
    )

    print("\n=== 2. Уровень 1: статусы сделок ===")
    status = run_status_batch(settings, db, llm, prompts, amo=None)
    for item in status.statuses:
        print(f"\n[примечание в сделку {item.lead_id or 'не найдена'}]")
        print(build_note(item.analysis, today))
        if item.analysis.next_step_overdue():
            print(f"[задача Данилу] Определить следующий шаг по {item.partner}")

    print("\n=== 3. Уровень 2: контроль качества ===")
    quality = run_quality_batch(settings, db, llm, prompts, amo=None, notifier=notifier)  # type: ignore[arg-type]
    for verdict in quality.verdicts:
        print(f"[{verdict.severity}] {verdict.chat}: {verdict.issue}")

    print("\n=== 4. Дайджест ===")
    digest = build_digest(settings, db, today)
    print(digest)

    summary = {
        "received": intake_result.received,
        "stored": intake_result.stored,
        "duplicates": intake_result.duplicates,
        "alerts": len(intake_result.alerts),
        "analyzed": status.analyzed,
        "verdicts": len(quality.verdicts),
        "digest": digest,
    }
    db.close()
    if tmp_dir is not None:
        tmp_dir.cleanup()
    return summary
