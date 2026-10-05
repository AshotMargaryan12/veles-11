"""X / Twitter — раздел 4.3 ТЗ.

Только официальный X API v2 по токену X_BEARER_TOKEN (тариф с доступом к чтению).
Скрейпинга нет: без токена или при ограничении доступа возвращается «данные
недоступны», а не ошибка.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import requests

from ..config import Settings
from ..models import (
    PLATFORM_X,
    STATUS_ERROR,
    STATUS_NOT_FOUND,
    STATUS_UNAVAILABLE,
    ContentItem,
    PlatformData,
    PlatformRef,
)

log = logging.getLogger(__name__)

API_BASE = "https://api.x.com/2"
TIMEOUT = 20
TWEETS_LIMIT = 20


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status(ref: PlatformRef, status: str, reason: str) -> PlatformData:
    return PlatformData(
        platform=PLATFORM_X, handle=ref.handle, url=ref.url,
        status=status, status_reason=reason, source="x_api",
    )


_ACCESS_REASONS = {
    401: "токен X отклонён (401)",
    403: "тариф X API не даёт читать эти данные (403)",
    429: "лимит запросов X API исчерпан (429)",
}


class XCollector:
    platform = PLATFORM_X

    def __init__(self, settings: Settings, session: Any = None):
        self.settings = settings
        self.session = session or requests.Session()

    def close(self) -> None:
        pass

    def _get(self, path: str, **params: Any) -> tuple[int, dict[str, Any]]:
        response = self.session.get(
            f"{API_BASE}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.settings.x_bearer_token}"},
            timeout=TIMEOUT,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body

    def collect(self, ref: PlatformRef) -> PlatformData:
        if not self.settings.x_bearer_token:
            return _status(
                ref, STATUS_UNAVAILABLE,
                "нет X_BEARER_TOKEN (без официального API X не отдаёт метрики)",
            )
        try:
            return self._collect(ref)
        except requests.RequestException as exc:
            return _status(ref, STATUS_ERROR, f"X API: сеть недоступна ({exc})")

    def _collect(self, ref: PlatformRef) -> PlatformData:
        code, body = self._get(
            f"users/by/username/{ref.handle}",
            **{"user.fields": "public_metrics,created_at,description,location,verified,name"},
        )
        if code in _ACCESS_REASONS:
            return _status(ref, STATUS_UNAVAILABLE, _ACCESS_REASONS[code])
        if code != 200 or not body.get("data"):
            if body.get("errors") or code == 404:
                return _status(ref, STATUS_NOT_FOUND, "профиль X не найден или заблокирован")
            return _status(ref, STATUS_ERROR, f"X API ответил {code}")

        user = body["data"]
        metrics = user.get("public_metrics") or {}
        data = PlatformData(
            platform=PLATFORM_X,
            handle=ref.handle,
            url=f"https://x.com/{user.get('username', ref.handle)}",
            title=user.get("name", ""),
            username=user.get("username", ref.handle),
            description=user.get("description", "") or "",
            followers=metrics.get("followers_count"),
            created_at=_parse_dt(user.get("created_at")),
            location=user.get("location") or None,
            items_limit=TWEETS_LIMIT,
            source="x_api",
        )

        code, tweets = self._get(
            f"users/{user['id']}/tweets",
            max_results=TWEETS_LIMIT,
            exclude="retweets,replies",
            **{"tweet.fields": "public_metrics,created_at"},
        )
        if code != 200:
            reason = _ACCESS_REASONS.get(code, f"X API ответил {code}")
            data.notes.append(f"посты недоступны: {reason} — только число фолловеров")
            return data

        for tweet in tweets.get("data") or []:
            pm = tweet.get("public_metrics") or {}
            data.items.append(
                ContentItem(
                    id=str(tweet.get("id")),
                    date=_parse_dt(tweet.get("created_at")),
                    text=tweet.get("text", ""),
                    views=pm.get("impression_count"),
                    reactions=(pm.get("like_count") or 0) + (pm.get("retweet_count") or 0),
                    comments=pm.get("reply_count"),
                    url=f"https://x.com/{data.username}/status/{tweet.get('id')}",
                )
            )
        return data
