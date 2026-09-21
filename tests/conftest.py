"""Общие фикстуры: фейковый Telegram-шлюз и генераторы снапшотов."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tghunter.models import Candidate, ChannelSnapshot, Post  # noqa: E402

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def make_posts(
    count: int = 12,
    views: int = 1000,
    reactions: int = 10,
    text: str = "Разбор рынка: акции, инвестиции и деньги для инвесторов",
    step_days: float = 2.0,
    start_days_ago: float = 0.0,
    now: datetime = NOW,
) -> list[Post]:
    return [
        Post(
            id=i + 1,
            date=now - timedelta(days=start_days_ago + i * step_days),
            text=text,
            views=views,
            reactions=reactions,
        )
        for i in range(count)
    ]


def make_snapshot(
    username: str = "good_channel",
    channel_id: int = 1001,
    subscribers: int = 10000,
    about: str = "Канал про инвестиции. По рекламе: @adv_manager",
    has_linked_chat: bool = True,
    posts: list[Post] | None = None,
    method: str = "keywords",
    source_channel: str | None = None,
    title: str = "Инвестиции просто",
) -> ChannelSnapshot:
    return ChannelSnapshot(
        channel_id=channel_id,
        username=username,
        title=title,
        about=about,
        subscribers=subscribers,
        has_linked_chat=has_linked_chat,
        posts=posts if posts is not None else make_posts(),
        method=method,
        source_channel=source_channel,
        fetched_at=NOW,
    )


class FakeGateway:
    """Подменяет TelegramGateway: отдаёт заранее заданные каналы, считает вызовы."""

    def __init__(
        self,
        snapshots: dict[str, ChannelSnapshot] | None = None,
        keyword_hits: dict[str, list[Candidate]] | None = None,
        similar_hits: dict[str, list[Candidate]] | None = None,
        mention_hits: dict[str, list[Candidate]] | None = None,
        folder_hits: dict[str, list[Candidate]] | None = None,
    ):
        self.snapshots = snapshots or {}
        self.keyword_hits = keyword_hits or {}
        self.similar_hits = similar_hits or {}
        self.mention_hits = mention_hits or {}
        self.folder_hits = folder_hits or {}
        self.fetch_calls: list[str] = []
        self.search_calls: list[str] = []

    def search_keyword(self, keyword, limit=30):
        self.search_calls.append(f"keyword:{keyword}")
        return list(self.keyword_hits.get(keyword, []))

    def similar_channels(self, username, limit=50):
        self.search_calls.append(f"similar:{username}")
        return list(self.similar_hits.get(username.lstrip("@"), []))

    def channel_mentions(self, username, posts_limit=50):
        self.search_calls.append(f"mentions:{username}")
        return list(self.mention_hits.get(username.lstrip("@"), []))

    def folder_channels(self, url):
        self.search_calls.append(f"folder:{url}")
        return list(self.folder_hits.get(url, []))

    def fetch_snapshot(self, username, posts_limit=30, method="manual", source_channel=None):
        self.fetch_calls.append(username)
        snapshot = self.snapshots.get(username.lstrip("@").lower())
        if snapshot is None:
            return None
        snapshot.method = method
        snapshot.source_channel = source_channel
        return snapshot

    def disconnect(self):
        pass


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def settings(tmp_path):
    from tghunter.config import Settings

    return Settings(
        api_id=1,
        api_hash="hash",
        db_path=str(tmp_path / "test.db"),
        export_dir=str(tmp_path / "exports"),
        config_dir="config",
        method_pause_min=0,
        method_pause_max=0,
    )


@pytest.fixture
def limiter():
    from tghunter.ratelimit import RateLimiter

    # нулевые паузы: тесты не должны ждать реальные 2-3 секунды
    return RateLimiter(min_interval=0, max_interval=0, sleeper=lambda _s: None)


@pytest.fixture
def db(settings):
    from tghunter.db import Database

    database = Database(settings.db_path)
    yield database
    database.close()


@pytest.fixture
def streams():
    from tghunter.config import load_streams

    return load_streams("config/streams.yaml")
