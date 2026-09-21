"""Слой доступа к Telegram через Telethon (MTProto, юзер-сессия).

Единственный модуль, который импортирует Telethon. Наружу отдаёт нормализованные
структуры из `tghunter.models`, поэтому остальной код тестируется без сети.

ТОЛЬКО ЧТЕНИЕ публичных данных: никаких вступлений в каналы, сообщений или
парсинга участников (раздел 9.3 ТЗ).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from .config import Settings
from .models import Candidate, ChannelSnapshot, Post
from .ratelimit import RateLimiter
from .textscan import extract_urls

log = logging.getLogger(__name__)

T = TypeVar("T")


class TelegramUnavailable(RuntimeError):
    """Telethon не установлен или сессия не создана."""


def _import_telethon():
    """Отложенный импорт: без Telethon остальной пакет остаётся работоспособным."""
    try:
        from telethon import TelegramClient, functions, types
        from telethon.errors import FloodWaitError
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise TelegramUnavailable(
            "Не установлен Telethon. Выполните: pip install -r requirements.txt"
        ) from exc
    return TelegramClient, functions, types, FloodWaitError


class TelegramGateway:
    """Обёртка над Telethon с троттлингом и обработкой FloodWait."""

    def __init__(self, settings: Settings, limiter: RateLimiter):
        self.settings = settings
        self.limiter = limiter
        (
            self._TelegramClient,
            self._functions,
            self._types,
            self._FloodWaitError,
        ) = _import_telethon()
        Path(settings.session_dir).mkdir(parents=True, exist_ok=True)
        self.client = self._TelegramClient(
            settings.session_path, settings.api_id, settings.api_hash
        )

    # --- жизненный цикл ---------------------------------------------------

    def __enter__(self) -> "TelegramGateway":
        self.connect()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.disconnect()

    def connect(self) -> None:
        if not self.settings.api_id or not self.settings.api_hash:
            raise TelegramUnavailable(
                "Не заданы TG_API_ID / TG_API_HASH — заполните .env"
            )
        self.client.connect()
        if not self.client.is_user_authorized():
            raise TelegramUnavailable(
                f"Сессия {self.settings.session_path}.session не авторизована. "
                "Создайте её командой: python -m tghunter login"
            )

    def disconnect(self) -> None:
        try:
            self.client.disconnect()
        except Exception:  # pragma: no cover
            pass

    # --- низкий уровень ---------------------------------------------------

    def _call(self, fn: Callable[[], T], what: str) -> Optional[T]:
        """Выполняет запрос с троттлингом и повтором после FloodWait."""
        for attempt in (1, 2):
            self.limiter.wait()
            try:
                result = fn()
                self.limiter.note_success()
                return result
            except self._FloodWaitError as exc:
                # note_floodwait сам бросит RunAborted на серии FloodWait
                self.limiter.note_floodwait(getattr(exc, "seconds", 60))
                if attempt == 2:
                    log.warning("%s: повтор после FloodWait не удался", what)
                    return None
            except Exception as exc:
                log.warning("%s: %s", what, exc)
                return None
        return None

    def _run(self, coro_factory: Callable[[], Any], what: str) -> Any:
        """Синхронный вызов корутины Telethon через его собственный loop."""
        return self._call(lambda: self.client.loop.run_until_complete(coro_factory()), what)

    # --- методы поиска ----------------------------------------------------

    def search_keyword(self, keyword: str, limit: int = 30) -> list[Candidate]:
        """Метод 1: глобальный поиск Telegram по ключевому слову."""
        functions, types = self._functions, self._types

        result = self._run(
            lambda: self.client(
                functions.contacts.SearchRequest(q=keyword, limit=limit)
            ),
            f"keyword search '{keyword}'",
        )
        if result is None:
            return []

        candidates: list[Candidate] = []
        for chat in list(getattr(result, "chats", [])):
            if not isinstance(chat, types.Channel) or not getattr(chat, "broadcast", False):
                continue  # берём только публичные каналы, не группы
            if not getattr(chat, "username", None):
                continue
            candidates.append(
                Candidate(
                    username=chat.username,
                    channel_id=chat.id,
                    method="keywords",
                    source_channel=keyword,
                    title=getattr(chat, "title", None),
                )
            )
        return candidates

    def similar_channels(self, username: str, limit: int = 50) -> list[Candidate]:
        """Метод 2: нативные рекомендации Telegram (channels.GetChannelRecommendations)."""
        functions, types = self._functions, self._types

        entity = self.resolve(username)
        if entity is None:
            return []

        result = self._run(
            lambda: self.client(
                functions.channels.GetChannelRecommendationsRequest(channel=entity)
            ),
            f"similar to @{username}",
        )
        if result is None:
            return []

        candidates: list[Candidate] = []
        for chat in list(getattr(result, "chats", []))[:limit]:
            if not isinstance(chat, types.Channel) or not getattr(chat, "username", None):
                continue
            candidates.append(
                Candidate(
                    username=chat.username,
                    channel_id=chat.id,
                    method="similar",
                    source_channel=f"@{username}",
                    title=getattr(chat, "title", None),
                )
            )
        return candidates

    def channel_mentions(self, username: str, posts_limit: int = 50) -> list[Candidate]:
        """Метод 3: форварды, t.me-ссылки и @упоминания из последних постов."""
        from .discovery.mentions import candidates_from_posts

        snapshot = self.fetch_snapshot(username, posts_limit=posts_limit)
        if snapshot is None:
            return []
        return candidates_from_posts(snapshot, posts_limit=posts_limit)

    def folder_channels(self, addlist_url: str) -> list[Candidate]:
        """Метод 4: разбор папки-подборки t.me/addlist/*."""
        functions, types = self._functions, self._types
        slug = addlist_url.rstrip("/").split("/")[-1]

        result = self._run(
            lambda: self.client(functions.chatlists.CheckChatlistInviteRequest(slug=slug)),
            f"addlist {slug}",
        )
        if result is None:
            return []

        candidates: list[Candidate] = []
        for chat in list(getattr(result, "chats", [])):
            if not isinstance(chat, types.Channel) or not getattr(chat, "broadcast", False):
                continue
            if not getattr(chat, "username", None):
                continue
            candidates.append(
                Candidate(
                    username=chat.username,
                    channel_id=chat.id,
                    method="folders",
                    source_channel=addlist_url,
                    title=getattr(chat, "title", None),
                )
            )
        return candidates

    # --- сбор данных канала -----------------------------------------------

    def resolve(self, username: str) -> Any:
        """Получает entity канала по username (только чтение)."""
        clean = username.lstrip("@")
        return self._run(lambda: self.client.get_entity(clean), f"resolve @{clean}")

    def fetch_snapshot(
        self,
        username: str,
        posts_limit: int = 30,
        method: str = "manual",
        source_channel: Optional[str] = None,
    ) -> Optional[ChannelSnapshot]:
        """Собирает профиль канала и последние посты в ChannelSnapshot."""
        functions, types = self._functions, self._types

        entity = self.resolve(username)
        if entity is None or not isinstance(entity, types.Channel):
            return None
        if not getattr(entity, "broadcast", False):
            return None  # это группа, а не канал

        full = self._run(
            lambda: self.client(functions.channels.GetFullChannelRequest(channel=entity)),
            f"full @{username}",
        )
        if full is None:
            return None

        full_chat = full.full_chat
        messages = self._run(
            lambda: self._collect_messages(entity, posts_limit),
            f"messages @{username}",
        )

        return ChannelSnapshot(
            channel_id=int(entity.id),
            username=getattr(entity, "username", None),
            title=getattr(entity, "title", "") or "",
            about=getattr(full_chat, "about", "") or "",
            subscribers=int(getattr(full_chat, "participants_count", 0) or 0),
            has_linked_chat=bool(getattr(full_chat, "linked_chat_id", None)),
            is_broadcast=True,
            verified=bool(getattr(entity, "verified", False)),
            restricted=bool(getattr(entity, "restricted", False)),
            posts=list(messages or []),
            method=method,
            source_channel=source_channel,
            fetched_at=datetime.now(timezone.utc),
        )

    async def _collect_messages(self, entity: Any, limit: int) -> list[Post]:
        """Читает последние посты канала и нормализует их в Post."""
        posts: list[Post] = []
        async for message in self.client.iter_messages(entity, limit=limit):
            if message is None or getattr(message, "date", None) is None:
                continue
            posts.append(_to_post(message))
        return posts


def _to_post(message: Any) -> Post:
    """Telethon Message -> Post. Вынесено, чтобы тестировать на фейках."""
    text = getattr(message, "message", "") or ""

    reactions = 0
    reactions_obj = getattr(message, "reactions", None)
    if reactions_obj is not None:
        for item in getattr(reactions_obj, "results", None) or []:
            reactions += int(getattr(item, "count", 0) or 0)

    fwd_channel_id = None
    fwd_username = None
    fwd = getattr(message, "fwd_from", None)
    if fwd is not None:
        from_id = getattr(fwd, "from_id", None)
        fwd_channel_id = getattr(from_id, "channel_id", None)
        fwd_username = getattr(fwd, "from_name", None)

    date = message.date
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    return Post(
        id=int(getattr(message, "id", 0) or 0),
        date=date,
        text=text,
        views=getattr(message, "views", None),
        reactions=reactions,
        forward_from_channel_id=fwd_channel_id,
        forward_from_username=fwd_username,
        urls=extract_urls(text),
    )
