"""Структуры данных, не зависящие от Telethon.

Слой `tghunter.tg` превращает объекты Telethon в эти дата-классы, всё остальное
(метрики, скоринг, фильтры, выгрузки) работает уже только с ними. Благодаря
этому логику можно тестировать без сети и без установленного Telethon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Методы поиска каналов (раздел 3 ТЗ)
METHOD_KEYWORDS = "keywords"
METHOD_SIMILAR = "similar"
METHOD_MENTIONS = "mentions"
METHOD_FOLDERS = "folders"
METHOD_MANUAL = "manual"

ALL_METHODS = (
    METHOD_KEYWORDS,
    METHOD_SIMILAR,
    METHOD_MENTIONS,
    METHOD_FOLDERS,
    METHOD_MANUAL,
)

# Статусы канала в базе (раздел 7 ТЗ)
STATUS_NEW = "new"
STATUS_EXPORTED = "exported"
STATUS_BLACKLIST = "blacklist"
STATUS_NO_CONTACT = "no_contact"
STATUS_FILTERED = "filtered"
STATUS_IMPORTED = "imported"


@dataclass
class Candidate:
    """Кандидат, найденный одним из методов поиска — до сбора метрик."""

    username: Optional[str] = None
    channel_id: Optional[int] = None
    method: str = METHOD_MANUAL
    source_channel: Optional[str] = None
    title: Optional[str] = None

    def key(self) -> str:
        if self.username:
            return self.username.lower()
        return f"id:{self.channel_id}"


@dataclass
class Post:
    """Один пост канала в нормализованном виде."""

    id: int
    date: datetime
    text: str = ""
    views: Optional[int] = None
    reactions: int = 0
    forward_from_channel_id: Optional[int] = None
    forward_from_username: Optional[str] = None
    urls: list[str] = field(default_factory=list)

    def age_days(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.date).total_seconds() / 86400.0


@dataclass
class ChannelSnapshot:
    """Сырой срез канала: профиль + последние посты.

    Это единственный вход для слоя метрик (см. `tghunter.metrics`).
    """

    channel_id: int
    username: Optional[str]
    title: str = ""
    about: str = ""
    subscribers: int = 0
    has_linked_chat: bool = False
    is_broadcast: bool = True
    verified: bool = False
    restricted: bool = False
    posts: list[Post] = field(default_factory=list)
    method: str = METHOD_MANUAL
    source_channel: Optional[str] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def link(self) -> str:
        if self.username:
            return f"https://t.me/{self.username}"
        return f"https://t.me/c/{self.channel_id}"
