"""Telegram через Telethon (MTProto, юзер-сессия) — раздел 4.1 ТЗ.

Переиспользует шлюз `tghunter.tg.TelegramGateway`: троттлинг не чаще одного
запроса в 2-3 секунды, ожидание FloodWait, аварийная остановка на серии
FloodWait. ТОЛЬКО ЧТЕНИЕ публичных данных: без вступления в каналы, без
сообщений, без парсинга участников.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tghunter.models import Post

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


def post_to_item(post: Post, username: Optional[str]) -> ContentItem:
    return ContentItem(
        id=str(post.id),
        date=post.date,
        text=post.text or "",
        views=post.views,
        reactions=post.reactions,
        comments=post.comments,
        group_id=str(post.grouped_id) if post.grouped_id else None,
        url=f"https://t.me/{username}/{post.id}" if username else None,
    )


def build_platform_data(
    ref: PlatformRef,
    entity: Any,
    full_chat: Any,
    posts: list[Post],
    posts_limit: int,
    linked_members: Optional[int] = None,
    linked_title: Optional[str] = None,
    notes: Optional[list[str]] = None,
) -> PlatformData:
    """Объекты Telethon -> PlatformData. Вынесено отдельно ради тестов на фейках."""
    username = getattr(entity, "username", None) or ref.handle
    is_group = bool(getattr(entity, "megagroup", False)) and not getattr(entity, "broadcast", False)
    participants = getattr(full_chat, "participants_count", None)
    if participants is None:
        participants = getattr(entity, "participants_count", None)
    created = getattr(entity, "date", None)
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    data = PlatformData(
        platform=PLATFORM_TELEGRAM,
        handle=ref.handle,
        url=f"https://t.me/{username}",
        kind=KIND_GROUP if is_group else KIND_CHANNEL,
        title=getattr(entity, "title", "") or "",
        username=username,
        description=getattr(full_chat, "about", "") or "",
        followers=int(participants) if participants is not None else None,
        created_at=created if isinstance(created, datetime) else None,
        items=[post_to_item(p, username) for p in posts],
        items_limit=posts_limit,
        source="telethon",
        notes=list(notes or []),
    )
    if is_group:
        data.has_community_chat = True
        data.community_members = data.followers
        data.community_title = data.title
    else:
        data.has_community_chat = bool(getattr(full_chat, "linked_chat_id", None))
        data.community_members = linked_members
        data.community_title = linked_title
    return data


def _status(ref: PlatformRef, status: str, reason: str) -> PlatformData:
    return PlatformData(
        platform=PLATFORM_TELEGRAM,
        handle=ref.handle,
        url=ref.url,
        status=status,
        status_reason=reason,
        source="telethon",
    )


class TelegramCollector:
    platform = PLATFORM_TELEGRAM

    def __init__(self, settings: Settings, gateway_factory: Optional[Callable[[], Any]] = None):
        self.settings = settings
        self._factory = gateway_factory or self._default_gateway
        self._gateway: Any = None
        self._unavailable: Optional[str] = None
        self.fetched = 0

    # --- шлюз ------------------------------------------------------------

    def _default_gateway(self) -> Any:
        from tghunter.config import Settings as HunterSettings
        from tghunter.ratelimit import RateLimiter
        from tghunter.tg import TelegramGateway

        hunter = HunterSettings(
            api_id=self.settings.tg_api_id,
            api_hash=self.settings.tg_api_hash,
            session_name=self.settings.tg_session_name,
            session_dir=self.settings.tg_session_dir,
        )
        limiter = RateLimiter(
            min_interval=self.settings.rate_min_interval,
            max_interval=self.settings.rate_max_interval,
            floodwait_abort_streak=self.settings.floodwait_abort_streak,
        )
        gateway = TelegramGateway(hunter, limiter)
        gateway.connect()
        return gateway

    def gateway(self) -> Any:
        if self._gateway is None:
            self._gateway = self._factory()
        return self._gateway

    def close(self) -> None:
        if self._gateway is not None:
            try:
                self._gateway.disconnect()
            finally:
                self._gateway = None

    # --- сбор ------------------------------------------------------------

    def collect(self, ref: PlatformRef) -> PlatformData:
        if not self.settings.telegram_configured:
            return _status(
                ref, STATUS_UNAVAILABLE,
                "не заданы TG_API_ID / TG_API_HASH в .env — Telegram не опрашивается",
            )
        if self._unavailable:
            return _status(ref, STATUS_UNAVAILABLE, self._unavailable)

        from tghunter.tg import TelegramUnavailable

        try:
            gateway = self.gateway()
        except TelegramUnavailable as exc:
            self._unavailable = str(exc)
            return _status(ref, STATUS_UNAVAILABLE, self._unavailable)

        self.fetched += 1
        return self._fetch(gateway, ref)

    def _fetch(self, gw: Any, ref: PlatformRef) -> PlatformData:
        functions, types = gw.functions, gw.types
        limit = self.settings.tg_posts_limit
        notes: list[str] = []

        entity = gw.resolve(ref.handle)
        if entity is None:
            return _status(
                ref, STATUS_NOT_FOUND,
                f"@{ref.handle} не найден или закрыт (приватные каналы не читаются)",
            )

        if isinstance(entity, types.User):
            full_user = gw.run(
                lambda: gw.client(functions.users.GetFullUserRequest(id=entity)),
                f"full user @{ref.handle}",
            )
            channel_id = getattr(getattr(full_user, "full_user", None), "personal_channel_id", None)
            channel = None
            if channel_id:
                channel = next(
                    (c for c in getattr(full_user, "chats", []) or [] if getattr(c, "id", None) == channel_id),
                    None,
                )
            if channel is None:
                return _status(
                    ref, STATUS_UNAVAILABLE,
                    f"@{ref.handle} — личный профиль, а не канал. Укажите ссылку на канал блогера",
                )
            notes.append(f"@{ref.handle} — профиль; взят его личный канал @{getattr(channel, 'username', '')}")
            entity = channel

        if not isinstance(entity, types.Channel):
            return _status(ref, STATUS_UNAVAILABLE, f"@{ref.handle} — не канал и не группа")

        full = gw.run(
            lambda: gw.client(functions.channels.GetFullChannelRequest(channel=entity)),
            f"full @{ref.handle}",
        )
        if full is None:
            return _status(ref, STATUS_ERROR, f"не удалось получить профиль @{ref.handle}")
        full_chat = full.full_chat

        posts = gw.run(lambda: gw._collect_messages(entity, limit), f"messages @{ref.handle}") or []

        linked_members = linked_title = None
        linked_id = getattr(full_chat, "linked_chat_id", None)
        is_broadcast = bool(getattr(entity, "broadcast", False))
        if linked_id and is_broadcast:
            linked = next((c for c in getattr(full, "chats", []) or [] if getattr(c, "id", None) == linked_id), None)
            if linked is not None:
                linked_title = getattr(linked, "title", None)
                linked_full = gw.run(
                    lambda: gw.client(functions.channels.GetFullChannelRequest(channel=linked)),
                    f"linked chat of @{ref.handle}",
                )
                if linked_full is not None:
                    linked_members = getattr(linked_full.full_chat, "participants_count", None)
                if linked_members is None:
                    linked_members = getattr(linked, "participants_count", None)

        return build_platform_data(
            ref, entity, full_chat, posts, limit,
            linked_members=linked_members, linked_title=linked_title, notes=notes,
        )
