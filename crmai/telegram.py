"""Telegram-бот: мгновенные алерты и дневной дайджест (раздел 3.3 ТЗ).

Обычный Bot API, без библиотек. Получатель по умолчанию один — руководитель;
менеджер подключается флагом SEND_TO_MANAGER=true.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Optional

from .config import Settings
from .retry import retry_call

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
# Лимит Bot API на одно сообщение
MAX_MESSAGE_LEN = 4096
SEND_TIMEOUT = 30


def escape(text: str) -> str:
    """Экранирование под parse_mode=HTML — тексты партнёров содержат что угодно."""
    return html.escape(text or "", quote=False)


def split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Режет длинный дайджест по строкам, чтобы влезть в лимит Bot API."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


class TelegramNotifier:
    """Отправка сообщений. Ошибка доставки логируется, но не роняет батч."""

    def __init__(self, settings: Settings, session: Optional[Any] = None):
        self.settings = settings
        self._session = session
        self.sent: list[tuple[str, str]] = []

    @property
    def http(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _send_one(self, chat_id: str, text: str) -> bool:
        def _call() -> Any:
            return self.http.post(
                f"{TELEGRAM_API}/bot{self.settings.tg_bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=SEND_TIMEOUT,
            )

        try:
            response = retry_call(_call, what=f"сообщение в Telegram {chat_id}")
        except Exception as exc:  # noqa: BLE001 — доставка необязательна для батча
            log.warning("Telegram: не отправили сообщение в %s: %s", chat_id, exc)
            return False

        if getattr(response, "status_code", 0) != 200:
            log.warning(
                "Telegram: сообщение в %s вернуло HTTP %s",
                chat_id, getattr(response, "status_code", "?"),
            )
            return False
        self.sent.append((chat_id, text))
        return True

    def send(self, chat_id: str, text: str) -> bool:
        if not self.settings.tg_bot_token or not chat_id:
            log.info("Telegram отключён (нет TG_BOT_TOKEN или чата) — сообщение не отправлено")
            return False
        ok = True
        for chunk in split_message(text):
            ok = self._send_one(chat_id, chunk) and ok
        return ok

    def broadcast(self, text: str) -> int:
        """Шлёт всем получателям из настроек. Возвращает число успешных отправок."""
        return sum(1 for chat_id in self.settings.alert_chats if self.send(chat_id, text))


def format_alert(
    title: str,
    partner: str,
    quote: str,
    lead_url: str = "",
    note: str = "",
    severity: str = "",
    suggestion: str = "",
) -> str:
    """Текст мгновенного алерта: правило, партнёр, цитата, ссылка на сделку."""
    head = f"🚨 <b>{escape(title)}</b>"
    if severity:
        head += f" [{escape(severity)}]"
    lines = [head, f"Партнёр: {escape(partner)}"]
    if quote:
        lines.append(f"Цитата: «{escape(quote)}»")
    if note:
        lines.append(f"⚠️ {escape(note)}")
    if suggestion:
        lines.append(f"💡 {escape(suggestion)}")
    if lead_url:
        lines.append(lead_url)
    else:
        lines.append("Сделка в amo не найдена")
    return "\n".join(lines)
