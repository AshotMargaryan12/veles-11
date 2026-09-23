"""Источник TGStat — работает по API-ключу, без юзер-сессии Telegram.

Документация: https://api.tgstat.ru/docs/ru/start/intro.html
Используются методы:
    channels/search  — поиск каналов по ключевому слову
    channels/posts   — последние посты (+ профиль канала при extended=1)

Чего TGStat не отдаёт по сравнению с MTProto: реакции и наличие чата
комментариев. Поэтому фильтр «ER >= 3% ИЛИ живые комментарии» здесь работает
только левой половиной, а скоринг не начисляет +10 за живое комьюнити.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import METHOD_KEYWORDS, Candidate, ChannelSnapshot, Post
from ..ratelimit import RateLimiter
from ..textscan import extract_urls

log = logging.getLogger(__name__)

API_BASE = "https://api.tgstat.ru"
DEFAULT_TIMEOUT = 30
# TGStat отдаёт максимум 100 каналов на запрос поиска и 50 постов на запрос
MAX_SEARCH_LIMIT = 100
MAX_POSTS_LIMIT = 50


class TGStatError(RuntimeError):
    """Ошибка TGStat: неверный токен, исчерпан лимит тарифа и т.п."""


class TGStatGateway:
    """Шлюз к TGStat. Подставляется в конвейер вместо Telegram."""

    name = "tgstat"

    def __init__(
        self,
        token: str,
        limiter: Optional[RateLimiter] = None,
        session: Optional[Any] = None,
        search_by_description: bool = True,
        language: Optional[str] = None,
    ):
        if not token:
            raise TGStatError("Не задан TGSTAT_TOKEN — вставьте ключ из личного кабинета TGStat")
        self.token = token
        self.limiter = limiter
        self.search_by_description = search_by_description
        self.language = language
        self.fetch_calls: list[str] = []
        self.search_calls: list[str] = []

        if session is not None:
            self.http = session
        else:
            import requests

            self.http = requests.Session()

    # --- низкий уровень ---------------------------------------------------

    def _get(self, method: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Запрос к API. Возвращает тело `response` или None при ошибке."""
        if self.limiter is not None:
            self.limiter.wait()

        payload = dict(params)
        payload["token"] = self.token
        try:
            response = self.http.get(
                f"{API_BASE}/{method}", params=payload, timeout=DEFAULT_TIMEOUT
            )
        except Exception as exc:
            log.warning("TGStat %s: сеть недоступна: %s", method, exc)
            return None

        status_code = getattr(response, "status_code", 0)
        if status_code != 200:
            log.warning("TGStat %s: HTTP %s", method, status_code)
            return None

        try:
            body = response.json() or {}
        except Exception as exc:
            log.warning("TGStat %s: не разобрать ответ: %s", method, exc)
            return None

        if body.get("status") != "ok":
            error = body.get("error") or body.get("message") or "неизвестная ошибка"
            log.warning("TGStat %s: %s", method, error)
            if "token" in str(error).lower():
                raise TGStatError(f"TGStat отверг токен: {error}")
            return None

        if self.limiter is not None:
            self.limiter.note_success()
        return body.get("response") or {}

    def check(self) -> tuple[bool, str]:
        """Проверка ключа — для формы настроек и команды setup."""
        try:
            result = self._get("channels/search", {"q": "новости", "limit": 1})
        except TGStatError as exc:
            return False, str(exc)
        if result is None:
            return False, "TGStat не ответил или отверг запрос — проверьте ключ и тариф"
        return True, "ключ принят"

    # --- интерфейс шлюза --------------------------------------------------

    def search_keyword(self, keyword: str, limit: int = 30) -> list[Candidate]:
        keyword = (keyword or "").strip()
        self.search_calls.append(f"keyword:{keyword}")
        if len(keyword) < 3:
            log.warning("TGStat: запрос '%s' короче 3 символов — пропускаем", keyword)
            return []

        params: dict[str, Any] = {
            "q": keyword,
            "limit": min(limit, MAX_SEARCH_LIMIT),
            "peer_type": "channel",
            "search_by_description": 1 if self.search_by_description else 0,
        }
        if self.language:
            params["language"] = self.language

        result = self._get("channels/search", params)
        if not result:
            return []

        candidates: list[Candidate] = []
        for item in result.get("items") or []:
            username = (item.get("username") or "").lstrip("@")
            if not username:
                continue  # приватный канал без username обработать нельзя
            candidates.append(
                Candidate(
                    username=username,
                    channel_id=item.get("tg_id") or item.get("id"),
                    method=METHOD_KEYWORDS,
                    source_channel=keyword,
                    title=item.get("title"),
                )
            )
        return candidates

    def similar_channels(self, username: str, limit: int = 50) -> list[Candidate]:
        # У TGStat нет аналога channels.GetChannelRecommendations.
        log.debug("TGStat: метод similar недоступен, нужен источник telegram")
        return []

    def channel_mentions(self, username: str, posts_limit: int = 50) -> list[Candidate]:
        from ..discovery.mentions import candidates_from_posts

        snapshot = self.fetch_snapshot(username, posts_limit=posts_limit)
        if snapshot is None:
            return []
        return candidates_from_posts(snapshot, posts_limit=posts_limit)

    def folder_channels(self, url: str) -> list[Candidate]:
        log.debug("TGStat: разбор папок недоступен, нужен источник telegram")
        return []

    def fetch_snapshot(
        self,
        username: str,
        posts_limit: int = 30,
        method: str = "manual",
        source_channel: Optional[str] = None,
    ) -> Optional[ChannelSnapshot]:
        """Профиль и посты канала одним запросом (channels/posts?extended=1)."""
        clean = (username or "").lstrip("@")
        self.fetch_calls.append(clean)
        if not clean:
            return None

        result = self._get(
            "channels/posts",
            {
                "channelId": f"@{clean}",
                "limit": min(posts_limit, MAX_POSTS_LIMIT),
                "extended": 1,
                "hideDeleted": 1,
            },
        )
        if not result:
            return None

        channel = result.get("channel") or {}
        posts = [_to_post(item) for item in (result.get("items") or [])]
        posts = [p for p in posts if p is not None]

        subscribers = int(channel.get("participants_count") or 0)
        if not subscribers:
            # профиль пришёл без счётчика — добираем его отдельным запросом
            stat = self._get("channels/stat", {"channelId": f"@{clean}"}) or {}
            subscribers = int(stat.get("participants_count") or 0)

        return ChannelSnapshot(
            channel_id=int(channel.get("tg_id") or channel.get("id") or 0),
            username=(channel.get("username") or clean).lstrip("@"),
            title=channel.get("title") or "",
            about=channel.get("about") or "",
            subscribers=subscribers,
            # TGStat не сообщает о чате комментариев и не отдаёт реакции
            has_linked_chat=False,
            posts=posts,
            method=method,
            source_channel=source_channel,
            fetched_at=datetime.now(timezone.utc),
        )

    def disconnect(self) -> None:
        close = getattr(self.http, "close", None)
        if callable(close):
            close()


def _to_post(item: dict[str, Any]) -> Optional[Post]:
    """Пост TGStat -> Post. Реакций в ответе нет, поэтому 0."""
    raw_date = item.get("date")
    if raw_date is None:
        return None
    try:
        date = datetime.fromtimestamp(int(raw_date), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

    text = item.get("text") or ""
    forwarded = item.get("forwarded_from")
    return Post(
        id=int(item.get("id") or 0),
        date=date,
        text=text,
        views=item.get("views"),
        reactions=0,
        forward_from_channel_id=forwarded if isinstance(forwarded, int) else None,
        forward_from_username=forwarded if isinstance(forwarded, str) else None,
        urls=extract_urls(text),
    )
