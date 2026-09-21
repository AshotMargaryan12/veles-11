"""Матчинг чата Wazzup на сделку в amoCRM (раздел 3.2 ТЗ).

Порядок:
  1. amo-идентификаторы из самого события Wazzup — самый надёжный источник:
     Wazzup сам заводит и связывает контакты в amo;
  2. иначе поиск контакта по username или телефону;
  3. у контакта берём ОТКРЫТЫЕ сделки в воронках PIPELINE_IDS, из них —
     последнюю по updated_at;
  4. сделки нет — помечаем чат unmatched, в дайджест идёт строкой, батч не падает.

Результат кэшируется в таблице chats на сутки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .amo import AmoClient, AmoError, pick_lead
from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


@dataclass
class Match:
    """Итог матчинга одного чата."""

    lead_id: Optional[int] = None
    contact_id: Optional[int] = None
    source: str = ""
    reason: str = ""

    @property
    def matched(self) -> bool:
        return bool(self.lead_id)


def _value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)


def search_queries(chat: Any) -> list[str]:
    """Чем искать контакт в amo: username, телефон, имя — в порядке точности."""
    queries: list[str] = []
    for key in ("contact_username", "contact_phone", "contact_name"):
        value = (_value(chat, key) or "").strip()
        if value and value not in queries:
            queries.append(value)
    return queries


def resolve_lead(
    db: Database,
    amo: AmoClient,
    chat: Any,
    settings: Settings,
    force: bool = False,
) -> Match:
    """Находит сделку для чата, используя кэш и, при промахе, поиск в amo."""
    chat_id = str(_value(chat, "chat_id"))

    if not force and db.match_is_fresh(chat):
        return Match(
            lead_id=int(_value(chat, "amo_lead_id")),
            contact_id=_value(chat, "amo_contact_id"),
            source="cache",
        )

    # 1. Идентификаторы пришли прямо в событии Wazzup
    direct_lead = _value(chat, "amo_lead_id")
    if direct_lead and not _value(chat, "matched_at"):
        db.set_match(chat_id, int(direct_lead), _value(chat, "amo_contact_id"))
        return Match(lead_id=int(direct_lead), contact_id=_value(chat, "amo_contact_id"),
                     source="wazzup")

    contact_id = _value(chat, "amo_contact_id")
    contacts: list[dict[str, Any]] = []

    # 2. Поиск контакта
    if contact_id:
        contacts = [{"id": int(contact_id)}]
    else:
        for query in search_queries(chat):
            try:
                found = amo.search_contacts(query)
            except AmoError as exc:
                log.warning("amo: поиск контакта по %r не удался: %s", query, exc)
                raise
            if found:
                contacts = found
                log.debug("amo: контакт найден по запросу %r", query)
                break

    if not contacts:
        db.set_match(chat_id, None, None, unmatched=True)
        return Match(reason="контакт не найден в amo")

    # 3. Открытые сделки контакта в нужных воронках
    for contact in contacts:
        cid = contact.get("id")
        if not cid:
            continue
        leads = ((contact.get("_embedded") or {}).get("leads") or [])
        detailed = amo.leads_by_ids([lead["id"] for lead in leads if lead.get("id")]) if leads else []
        if not detailed:
            detailed = amo.contact_leads(int(cid))
        lead = pick_lead(detailed, settings.pipeline_ids)
        if lead:
            db.set_match(chat_id, int(lead["id"]), int(cid))
            return Match(lead_id=int(lead["id"]), contact_id=int(cid), source="search")

    # 4. Контакт есть, открытой сделки нет
    fallback_contact = int(contacts[0].get("id") or 0) or None
    db.set_match(chat_id, None, fallback_contact, unmatched=True)
    return Match(contact_id=fallback_contact, reason="нет открытой сделки в PIPELINE_IDS")


def partner_name(chat: Any) -> str:
    """Как называть партнёра в примечаниях, алертах и дайджесте."""
    for key in ("contact_name", "contact_username", "chat_id"):
        value = (_value(chat, key) or "").strip()
        if value:
            return value
    return "без имени"
