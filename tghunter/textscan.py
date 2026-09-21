"""Разбор текста канала: контакты, маркеры монетизации, стоп-слова, упоминания.

Модуль чистый — принимает строки, отдаёт структуры. Используется слоем метрик
и методом поиска «граф упоминаний».
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# --- Контакты ---------------------------------------------------------------

# @username по правилам Telegram: 5-32 символа, буквы/цифры/подчёркивания
_USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{4,31})")
_TME_RE = re.compile(
    r"(?:https?://)?t\.me/(?!joinchat|\+|addlist|s/|c/)([A-Za-z][A-Za-z0-9_]{4,31})",
    re.IGNORECASE,
)
_TME_ANY_RE = re.compile(r"(?:https?://)?t\.me/(\S+)", re.IGNORECASE)
_ADDLIST_RE = re.compile(r"(?:https?://)?t\.me/addlist/([A-Za-z0-9_-]+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Подписи, рядом с которыми обычно стоит рекламный контакт
_CONTACT_HINTS = (
    "по рекламе",
    "реклама и сотрудничество",
    "сотрудничество",
    "по вопросам рекламы",
    "реклама:",
    "adv",
    "ads",
    "pr:",
    "связь",
    "контакт",
    "админ",
    "owner",
    "manager",
    "менеджер",
    "for ads",
    "рекламу",
)

# Служебные username Telegram, которые контактом не являются
_CONTACT_STOPWORDS = {
    "telegram", "durov", "premium", "telegramtips", "channel", "username",
}


def extract_usernames(text: str) -> list[str]:
    """Все @упоминания и t.me-ссылки в тексте, в нижнем регистре, без дублей."""
    if not text:
        return []
    found = [m.lower() for m in _USERNAME_RE.findall(text)]
    found += [m.lower() for m in _TME_RE.findall(text)]
    seen: set[str] = set()
    result: list[str] = []
    for name in found:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def extract_addlists(text: str) -> list[str]:
    """Ссылки на папки-подборки t.me/addlist/*."""
    return [f"https://t.me/addlist/{slug}" for slug in _ADDLIST_RE.findall(text or "")]


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def extract_contact(about: str) -> Optional[str]:
    """Достаёт контакт для связи из описания канала (раздел 4.7 ТЗ).

    Приоритет — username рядом с рекламной подписью («по рекламе: @...»),
    иначе первый валидный @username из описания.
    """
    if not about:
        return None

    candidates = [u for u in extract_usernames(about) if u not in _CONTACT_STOPWORDS]
    if not candidates:
        return None

    lowered = about.lower()
    best: Optional[str] = None
    best_distance = 10**6

    for name in candidates:
        pos = lowered.find(name)
        if pos < 0:
            continue
        for hint in _CONTACT_HINTS:
            hint_pos = lowered.rfind(hint, 0, pos)
            if hint_pos < 0:
                continue
            distance = pos - hint_pos
            # подпись должна быть рядом, а не в другом конце описания
            if distance < best_distance and distance <= 60:
                best, best_distance = name, distance

    return f"@{best or candidates[0]}"


# --- Монетизация ------------------------------------------------------------

_MONETIZATION_MARKERS = (
    "#реклама",
    "#промо",
    "#partner",
    "#ad",
    "#adv",
    "#sponsored",
    "erid",
    "реклама.",
    "на правах рекламы",
    "рекламный пост",
    "партнёрский материал",
    "партнерский материал",
    "промокод",
    "реферальн",
)

# Параметры, выдающие рекламную/партнёрскую ссылку
_REF_PARAMS = (
    "utm_source=",
    "utm_medium=",
    "utm_campaign=",
    "ref=",
    "ref_id=",
    "refid=",
    "referral=",
    "invite=",
    "promo=",
    "promocode=",
    "partner=",
    "aff=",
    "affiliate=",
    "clickid=",
    "sub_id=",
)

# Домены бирж/обменников/сервисов, ссылки на которые почти всегда рекламные
_AD_DOMAINS = (
    "binance.com",
    "bybit.com",
    "okx.com",
    "bitget.com",
    "mexc.com",
    "htx.com",
    "kucoin.com",
    "gate.io",
    "bingx.com",
    "veles.finance",
    "bestchange.ru",
    "garantex.io",
)


def detect_monetization(texts: Iterable[str]) -> list[str]:
    """Возвращает найденные маркеры «канал продаёт рекламу» (раздел 4.6 ТЗ)."""
    hits: list[str] = []
    seen: set[str] = set()

    for text in texts:
        if not text:
            continue
        low = text.lower()
        for marker in _MONETIZATION_MARKERS:
            if marker in low and marker not in seen:
                seen.add(marker)
                hits.append(marker)
        for url in extract_urls(text):
            url_low = url.lower()
            for param in _REF_PARAMS:
                if param in url_low and param not in seen:
                    seen.add(param)
                    hits.append(param)
            for domain in _AD_DOMAINS:
                if domain in url_low:
                    key = f"link:{domain}"
                    if key not in seen:
                        seen.add(key)
                        hits.append(key)
    return hits


# --- Стоп-маркеры -----------------------------------------------------------

def find_blacklist_markers(texts: Iterable[str], markers: Iterable[str]) -> list[str]:
    """Стоп-слова, найденные в текстах (раздел 4.8 ТЗ)."""
    markers = [m.lower() for m in markers if m]
    if not markers:
        return []
    blob = "\n".join(t.lower() for t in texts if t)
    if not blob:
        return []
    hits: list[str] = []
    for marker in markers:
        if marker in blob and marker not in hits:
            hits.append(marker)
    return hits
