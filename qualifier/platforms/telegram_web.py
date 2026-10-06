"""Telegram без входа в аккаунт: публичная веб-страница канала t.me/<name> и лента t.me/s/<name>.

Запасной путь, когда нет Telegram-сессии (не заданы TG_API_ID / TG_API_HASH или
не выполнен `qualify login`). Что даёт: точное число подписчиков, название,
описание, последние посты с датами, текстом, просмотрами и реакциями.
Чего не даёт: чат обсуждений и его участников, комментарии; просмотры и реакции
на странице округлены («1.8K»), поэтому карточка помечает их как приблизительные.

Только чтение публичных страниц, не чаще одного запроса в 2-3 секунды.
"""

from __future__ import annotations

import html
import logging
import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Optional

import requests

from ..config import Settings
from ..models import (
    KIND_CHANNEL,
    KIND_GROUP,
    PLATFORM_TELEGRAM,
    STATUS_ERROR,
    STATUS_NOT_FOUND,
    STATUS_UNAVAILABLE,
    ContentItem,
    PlatformData,
    PlatformRef,
)

log = logging.getLogger(__name__)

BASE = "https://t.me"
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; partner-qualifier/0.2)", "Accept-Language": "en"}

_COUNT_RE = re.compile(r"([\d\s.,]+)\s*([KMB])?", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_count(text: str) -> Optional[int]:
    """«10 498 219», «18.9M», «6.29K», «1,234» -> число."""
    if not text:
        return None
    match = _COUNT_RE.search(html.unescape(text).replace(" ", " "))
    if not match:
        return None
    raw, suffix = match.group(1).strip(), (match.group(2) or "").upper()
    if suffix:
        number = float(raw.replace(" ", "").replace(",", "."))
        return int(round(number * {"K": 1e3, "M": 1e6, "B": 1e9}[suffix]))
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _first(pattern: str, text: str, flags: int = re.S) -> Optional[str]:
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None


def parse_profile(page: str) -> dict[str, Any]:
    """Страница t.me/<name>: название, описание, подписчики/участники."""
    extra = _text(_first(r'class="tgme_page_extra">(.*?)</div>', page) or "")
    title = _text(_first(r'class="tgme_page_title"[^>]*>(.*?)</div>', page) or "")
    description = _text(_first(r'class="tgme_page_description[^"]*"[^>]*>(.*?)</div>', page) or "")
    kind = None
    count = None
    low = extra.lower()
    if "subscriber" in low:
        kind, count = KIND_CHANNEL, parse_count(extra.split("subscriber")[0])
    elif "member" in low:
        kind, count = KIND_GROUP, parse_count(extra.split("member")[0])
    return {"title": title, "description": description, "kind": kind, "count": count, "extra": extra}


def parse_feed(page: str, username: str) -> tuple[list[ContentItem], Optional[int]]:
    """Лента t.me/s/<name>: посты и id для следующей страницы (before=)."""
    items: list[ContentItem] = []
    for block in page.split('<div class="tgme_widget_message_wrap')[1:]:
        post = _first(r'data-post="[^"/]+/(\d+)"', block)
        if not post or "service_message" in block:
            continue
        stamp = _first(r'<time datetime="([^"]+)"', block)
        date = datetime.fromisoformat(stamp) if stamp else None
        views = parse_count(_first(r'class="tgme_widget_message_views">([^<]+)<', block) or "")
        text = _text(_first(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block) or "")
        reactions = 0
        reactions_block = _first(r'class="tgme_widget_message_reactions[^"]*">(.*?)</div>', block) or ""
        for count in re.findall(r'class="tgme_reaction(?! tgme_reaction_paid)[^"]*">.*?([\d.,]+[KMB]?)</span>',
                                reactions_block, re.S):
            reactions += parse_count(count) or 0
        items.append(ContentItem(
            id=post, date=date, text=text, views=views, reactions=reactions,
            url=f"{BASE}/{username}/{post}",
        ))
    before = _first(r'data-before="(\d+)"', page) or _first(r'href="/s/[^"?]+\?before=(\d+)"', page)
    return items, int(before) if before else None


class TelegramWebCollector:
    platform = PLATFORM_TELEGRAM

    def __init__(self, settings: Settings, session: Any = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.session = session or requests.Session()
        self.sleeper = sleeper
        self._last: Optional[float] = None

    def close(self) -> None:
        pass

    def _get(self, url: str) -> Any:
        # тот же темп, что и у MTProto-шлюза: не чаще раза в 2-3 секунды
        if self._last is not None:
            wait = random.uniform(self.settings.rate_min_interval, self.settings.rate_max_interval)
            elapsed = time.monotonic() - self._last
            if elapsed < wait:
                self.sleeper(wait - elapsed)
        self._last = time.monotonic()
        return self.session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)

    def collect(self, ref: PlatformRef) -> PlatformData:
        def status(code: str, reason: str) -> PlatformData:
            return PlatformData(platform=PLATFORM_TELEGRAM, handle=ref.handle, url=ref.url,
                                status=code, status_reason=reason, source="telegram_web")

        try:
            response = self._get(f"{BASE}/{ref.handle}")
        except requests.RequestException as exc:
            return status(STATUS_ERROR, f"t.me недоступен ({exc})")
        if response.status_code != 200:
            return status(STATUS_ERROR, f"t.me ответил {response.status_code}")
        profile = parse_profile(response.text)
        if profile["kind"] is None:
            if not profile["title"]:
                return status(STATUS_NOT_FOUND, f"@{ref.handle} не найден")
            return status(STATUS_UNAVAILABLE,
                          f"@{ref.handle} — не публичный канал или группа (возможно, личный профиль)")

        data = PlatformData(
            platform=PLATFORM_TELEGRAM, handle=ref.handle, url=f"{BASE}/{ref.handle}",
            kind=profile["kind"], title=profile["title"], username=ref.handle,
            description=profile["description"], followers=profile["count"],
            has_community_chat=True if profile["kind"] == KIND_GROUP else None,
            community_members=profile["count"] if profile["kind"] == KIND_GROUP else None,
            community_title=profile["title"] if profile["kind"] == KIND_GROUP else None,
            items_limit=self.settings.tg_posts_limit, source="telegram_web",
            notes=["без входа в Telegram: просмотры и реакции округлены, чат обсуждений не виден"],
        )
        if profile["kind"] == KIND_GROUP:
            data.notes.append("это группа: сообщения чата без входа не читаются")
            return data

        url = f"{BASE}/s/{ref.handle}"
        seen: set[str] = set()
        while url and len(data.items) < self.settings.tg_posts_limit:
            try:
                response = self._get(url)
            except requests.RequestException as exc:
                data.notes.append(f"лента прочитана не полностью ({exc})")
                break
            if response.status_code != 200 or "/s/" not in str(getattr(response, "url", url)):
                if not data.items:
                    data.notes.append("канал закрыл веб-превью ленты — постов без входа не видно")
                break
            items, before = parse_feed(response.text, ref.handle)
            fresh = [i for i in items if i.id not in seen]
            if not fresh:
                break
            seen.update(i.id for i in fresh)
            data.items.extend(fresh)
            url = f"{BASE}/s/{ref.handle}?before={before}" if before else None
        data.items.sort(key=lambda i: i.date.timestamp() if i.date else 0.0, reverse=True)
        data.items = data.items[: self.settings.tg_posts_limit]
        return data
