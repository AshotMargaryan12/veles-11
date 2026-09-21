"""Разбор вебхуков Wazzup (раздел 3.1 ТЗ).

Формат события сверяется с актуальной докой Wazzup API v3 при внедрении —
парсер намеренно терпим к именам полей: берёт первое непустое из списка
синонимов и не падает на незнакомых ключах, а кладёт всё событие в raw_json.

Приходит либо `{"messages": [...]}`, либо `{"test": true}` при проверке
эндпоинта из личного кабинета, либо события других типов (contacts, channels) —
их мы игнорируем, отвечая 200.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .models import DIRECTION_IN, DIRECTION_OUT, MEDIA_MARKER, Message

log = logging.getLogger(__name__)

# Статусы Wazzup, которые бывают только у исходящих сообщений
_OUTBOUND_STATUSES = {"sent", "delivered", "read", "error", "outbound"}
_INBOUND_STATUSES = {"inbound", "received"}

_DIGITS_RE = re.compile(r"^\+?\d{7,20}$")


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if "." in name:
            head, _, tail = name.partition(".")
            nested = payload.get(head)
            if isinstance(nested, dict):
                value = nested.get(tail)
                if value not in (None, "", []):
                    return value
            continue
        value = payload.get(name)
        if value not in (None, "", []):
            return value
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_ts(value: Any) -> str:
    """Любую форму даты Wazzup приводим к ISO-8601 в UTC."""
    text = _str(value)
    if not text:
        return datetime.now(timezone.utc).isoformat()
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:  # unix-время в секундах или миллисекундах
            number = float(text)
            if number > 1e11:
                number /= 1000
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            log.debug("Не разобрали дату события: %r", text)
            return datetime.now(timezone.utc).isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def detect_direction(event: dict[str, Any]) -> str:
    """Исходящее (наш менеджер) или входящее (партнёр).

    Признаки в порядке надёжности: явный `direction`, `isEcho` (Wazzup помечает
    так сообщения, отправленные из CRM или с телефона менеджера), затем статус
    доставки — он бывает только у исходящих.
    """
    direction = _str(_first(event, "direction")).lower()
    if direction in ("out", "outbound", "outgoing"):
        return DIRECTION_OUT
    if direction in ("in", "inbound", "incoming"):
        return DIRECTION_IN

    for flag in ("isEcho", "is_echo", "fromMe", "isFromMe", "outgoing"):
        value = event.get(flag)
        if isinstance(value, bool):
            return DIRECTION_OUT if value else DIRECTION_IN
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return DIRECTION_OUT if value.lower() == "true" else DIRECTION_IN

    status = _str(_first(event, "status")).lower()
    if status in _OUTBOUND_STATUSES:
        return DIRECTION_OUT
    if status in _INBOUND_STATUSES:
        return DIRECTION_IN

    # Неизвестный формат: считаем входящим — исходящие уходят в алерты и
    # замечания, ложное срабатывание там дороже пропуска.
    log.warning("Не определили направление сообщения %s, считаем входящим",
                _str(_first(event, "messageId", "id")))
    return DIRECTION_IN


def extract_text(event: dict[str, Any]) -> tuple[str, bool]:
    """Текст сообщения и флаг медиа.

    Голосовые и файлы не расшифровываем (MVP): сохраняем факт сообщения с
    пометкой [media], подпись к файлу, если она есть, оставляем.
    """
    text = _str(_first(event, "text", "body", "message"))
    kind = _str(_first(event, "type", "messageType")).lower()
    has_media = bool(_first(event, "contentUri", "content_uri", "fileUrl", "attachment"))
    is_media = has_media or (kind not in ("", "text", "chat"))

    if not is_media:
        return text, False
    marker = MEDIA_MARKER if not kind or kind == "text" else f"{MEDIA_MARKER} {kind}"
    return (f"{marker} {text}".strip() if text else marker), True


def parse_event(event: dict[str, Any]) -> Optional[Message]:
    """Одно событие Wazzup -> Message. None, если это не сообщение."""
    message_id = _str(_first(event, "messageId", "message_id", "id"))
    chat_id = _str(_first(event, "chatId", "chat_id"))
    if not message_id or not chat_id:
        log.debug("Событие без messageId/chatId пропущено: %r", sorted(event))
        return None

    text, is_media = extract_text(event)
    chat_type = _str(_first(event, "chatType", "chat_type"))
    username = _str(
        _first(event, "contact.username", "contact.telegramUsername", "username", "authorName")
    ).lstrip("@")
    phone = _str(_first(event, "contact.phone", "phone"))
    if not phone and chat_type.lower() == "whatsapp" and _DIGITS_RE.match(chat_id):
        phone = chat_id

    return Message(
        message_id=message_id,
        chat_id=chat_id,
        direction=detect_direction(event),
        text=text,
        ts=normalize_ts(_first(event, "dateTime", "datetime", "timestamp", "date")),
        chat_type=chat_type,
        contact_name=_str(_first(event, "contact.name", "contactName", "authorName")),
        contact_username=username,
        contact_phone=phone,
        # Wazzup сам заводит и связывает контакты в amo: если id пришёл в
        # событии — берём напрямую, это точнее любого поиска (раздел 3.2 ТЗ)
        amo_contact_id=_int(_first(event, "contact.crmId", "crmContactId", "amoContactId")),
        amo_lead_id=_int(_first(event, "contact.crmLeadId", "crmLeadId", "amoLeadId", "dealId")),
        is_media=is_media,
        raw=event,
    )


def parse_payload(payload: Any) -> list[Message]:
    """Тело вебхука -> список сообщений. Не-сообщения дают пустой список."""
    if isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict):
        if payload.get("test") is True:
            log.info("Wazzup: проверочный запрос эндпоинта")
            return []
        raw = payload.get("messages")
        if raw is None:
            # одиночное событие без конверта
            raw = [payload] if payload.get("messageId") or payload.get("chatId") else []
        events = raw if isinstance(raw, list) else [raw]
    else:
        return []

    messages: list[Message] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        message = parse_event(event)
        if message is not None:
            messages.append(message)
    return messages
