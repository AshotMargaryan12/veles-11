"""Приём сообщений и мгновенные алерты (разделы 4.1 и 4.2 ТЗ).

Вебхук отдаёт 200 сразу, а эта часть выполняется фоном: сохранить сообщение с
дедупом по message_id и, если оно исходящее, прогнать по стоп-паттернам.
Сработало — алерт руководителю немедленно, с цитатой и ссылкой на сделку.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .amo import AmoClient
from .config import Settings
from .db import Database
from .matching import partner_name, resolve_lead
from .models import Message, RedFlag
from .redflags import scan_message
from .telegram import TelegramNotifier, format_alert
from .wazzup import parse_payload

log = logging.getLogger(__name__)


@dataclass
class IntakeResult:
    """Что произошло с одной пачкой событий вебхука."""

    received: int = 0
    stored: int = 0
    duplicates: int = 0
    alerts: list[RedFlag] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "alerts": [flag.rule for flag in self.alerts],
        }


class Intake:
    """Обработчик входящих событий Wazzup."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        notifier: Optional[TelegramNotifier] = None,
        amo: Optional[AmoClient] = None,
    ):
        self.settings = settings
        self.db = db
        self.notifier = notifier
        self.amo = amo

    def handle_payload(self, payload: Any) -> IntakeResult:
        """Полное тело вебхука -> сохранение и алерты."""
        result = IntakeResult()
        for message in parse_payload(payload):
            result.received += 1
            if self.db.add_message(message):
                result.stored += 1
                result.alerts.extend(self.check_message(message))
            else:
                result.duplicates += 1
                log.debug("Дубль события %s — пропущен", message.message_id)
        if result.received:
            log.info(
                "Wazzup: получено %d, сохранено %d, дублей %d, алертов %d",
                result.received, result.stored, result.duplicates, len(result.alerts),
            )
        return result

    def check_message(self, message: Message) -> list[RedFlag]:
        """Regex-слой по исходящему сообщению + отправка алертов."""
        flags = scan_message(message)
        sent: list[RedFlag] = []
        for flag in flags:
            # Дедуп алертов: повторная доставка того же события не разбудит чат дважды
            if not self.db.register_alert(message.message_id, message.chat_id, flag.rule, flag.quote):
                continue
            if self.send_alert(message, flag):
                self.db.mark_alert_sent(message.message_id, flag.rule)
            sent.append(flag)
        return sent

    def send_alert(self, message: Message, flag: RedFlag) -> bool:
        chat = self.db.get_chat(message.chat_id)
        lead_id = self.lead_id_for(chat)
        text = format_alert(
            title=flag.title,
            partner=partner_name(chat) if chat is not None else message.contact_name,
            quote=flag.quote or message.text,
            lead_url=self.settings.lead_url(lead_id) if lead_id else "",
            note=flag.note,
        )
        log.warning("Стоп-паттерн %s в чате %s", flag.rule, message.chat_id)
        if self.notifier is None:
            log.info("Уведомления отключены — алерт только в лог")
            return False
        return self.notifier.broadcast(text) > 0

    def lead_id_for(self, chat: Any) -> Optional[int]:
        """ID сделки для ссылки в алерте: из кэша, иначе — разовый поиск в amo."""
        if chat is None:
            return None
        cached = chat["amo_lead_id"]
        if cached:
            return int(cached)
        if self.amo is None:
            return None
        try:
            return resolve_lead(self.db, self.amo, chat, self.settings).lead_id
        except Exception as exc:  # noqa: BLE001 — алерт важнее ссылки в нём
            log.warning("Матчинг для алерта не удался: %s", exc)
            return None
