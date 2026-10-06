"""Сборщики площадок на фейках: Telegram (объекты Telethon), YouTube API и yt-dlp, X API."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from qualifier.config import Settings
from qualifier.links import parse_link
from qualifier.platforms.telegram import TelegramCollector, build_platform_data
from qualifier.platforms.x import XCollector
from qualifier.platforms.youtube import YouTubeCollector, parse_duration
from tghunter.models import Post

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


# --- Telegram -----------------------------------------------------------------

def test_telegram_channel_normalized():
    entity = SimpleNamespace(username="masr", title="Crypto Masr", broadcast=True, megagroup=False,
                             date=datetime(2021, 1, 1))
    full_chat = SimpleNamespace(about="По рекламе: @ads", participants_count=13556, linked_chat_id=77)
    posts = [Post(id=5, date=NOW, text="пост", views=1800, reactions=40, grouped_id=9, comments=3)]
    data = build_platform_data(parse_link("t.me/masr"), entity, full_chat, posts, 50,
                               linked_members=1230, linked_title="Чат")
    assert data.followers == 13556
    assert data.kind == "channel"
    assert data.has_community_chat is True
    assert data.community_members == 1230
    assert data.items[0].group_id == "9"
    assert data.items[0].comments == 3
    assert data.items[0].url == "https://t.me/masr/5"
    assert data.created_at.tzinfo is not None


def test_telegram_group_is_community():
    entity = SimpleNamespace(username="masr_chat", title="Чат", broadcast=False, megagroup=True)
    full_chat = SimpleNamespace(about="", participants_count=9000, linked_chat_id=None)
    data = build_platform_data(parse_link("t.me/masr_chat"), entity, full_chat, [], 50)
    assert data.kind == "group"
    assert data.community_members == 9000


def test_telegram_without_keys_is_unavailable():
    collector = TelegramCollector(Settings(), gateway_factory=lambda: pytest.fail("не должен подключаться"))
    data = collector.collect(parse_link("t.me/masr"))
    assert data.status == "unavailable"
    assert "TG_API_ID" in data.status_reason


def test_telegram_session_problem_reported_once():
    from tghunter.tg import TelegramUnavailable

    calls = []

    def factory():
        calls.append(1)
        raise TelegramUnavailable("Сессия не авторизована")

    collector = TelegramCollector(Settings(tg_api_id=1, tg_api_hash="h"), gateway_factory=factory)
    first = collector.collect(parse_link("t.me/aaaaa"))
    second = collector.collect(parse_link("t.me/bbbbb"))
    assert first.status == second.status == "unavailable"
    assert "не авторизована" in second.status_reason
    assert len(calls) == 1


class _Types:
    class User:  # noqa: D106
        pass

    class Channel:  # noqa: D106
        pass


class _Requests:
    class channels:  # noqa: N801
        @staticmethod
        def GetFullChannelRequest(channel):  # noqa: N802
            return ("full", channel)

    class users:  # noqa: N801
        @staticmethod
        def GetFullUserRequest(id):  # noqa: N802,A002
            return ("user", id)


class FakeGateway:
    def __init__(self, entity, full, linked_full=None, user_full=None):
        self.types = _Types
        self.functions = _Requests
        self.entity, self.full, self.linked_full, self.user_full = entity, full, linked_full, user_full
        self.client = self._client

    def _client(self, request):
        kind, target = request
        if kind == "user":
            return self.user_full
        return self.full if target is self.entity else self.linked_full

    def resolve(self, handle):
        return self.entity

    def run(self, factory, what):
        result = factory()
        return result

    def _collect_messages(self, entity, limit):
        return [Post(id=1, date=NOW, text="пост", views=100)]

    def disconnect(self):
        pass


def _channel(**kw):
    ch = _Types.Channel()
    for k, v in {"username": "masr", "title": "Masr", "broadcast": True, "megagroup": False, "id": 1, **kw}.items():
        setattr(ch, k, v)
    return ch


def test_telegram_fetch_with_linked_chat():
    linked = _channel(id=77, username=None, title="Чат", broadcast=False, megagroup=True)
    channel = _channel()
    full = SimpleNamespace(full_chat=SimpleNamespace(about="", participants_count=5000, linked_chat_id=77),
                           chats=[channel, linked])
    linked_full = SimpleNamespace(full_chat=SimpleNamespace(participants_count=812))
    gw = FakeGateway(channel, full, linked_full)
    collector = TelegramCollector(Settings(tg_api_id=1, tg_api_hash="h"), gateway_factory=lambda: gw)
    data = collector.collect(parse_link("t.me/masr"))
    assert data.ok and data.followers == 5000
    assert data.community_members == 812 and data.community_title == "Чат"


def test_telegram_user_profile_without_channel():
    user = _Types.User()
    gw = FakeGateway(user, None, user_full=SimpleNamespace(full_user=SimpleNamespace(personal_channel_id=None), chats=[]))
    collector = TelegramCollector(Settings(tg_api_id=1, tg_api_hash="h"), gateway_factory=lambda: gw)
    data = collector.collect(parse_link("@someone"))
    assert data.status == "unavailable"
    assert "личный профиль" in data.status_reason


def test_telegram_user_profile_with_personal_channel():
    user = _Types.User()
    channel = _channel(id=55, username="someone_blog")
    full = SimpleNamespace(full_chat=SimpleNamespace(about="", participants_count=3000, linked_chat_id=None),
                           chats=[channel])
    gw = FakeGateway(user, full, user_full=SimpleNamespace(
        full_user=SimpleNamespace(personal_channel_id=55), chats=[channel]))
    gw.client = lambda req: gw.user_full if req[0] == "user" else full
    collector = TelegramCollector(Settings(tg_api_id=1, tg_api_hash="h"), gateway_factory=lambda: gw)
    data = collector.collect(parse_link("@someone"))
    assert data.ok and data.username == "someone_blog"
    assert "личный канал" in data.notes[0]


# --- YouTube ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        for key, response in self.routes.items():
            if key in url:
                return response(params) if callable(response) else response
        return FakeResponse(404, {})


def _yt_routes():
    channel = {"items": [{
        "id": "UC123", "snippet": {"title": "Crypto Masr", "description": "desc", "customUrl": "@masr",
                                   "publishedAt": "2020-01-01T00:00:00Z", "country": "EG"},
        "statistics": {"subscriberCount": "4200"},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
    }]}
    playlist = {"items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(3)]}
    videos = {"items": [
        {"id": "v0", "snippet": {"title": "Видео", "publishedAt": "2026-10-01T00:00:00Z"},
         "statistics": {"viewCount": "900", "likeCount": "50", "commentCount": "5"},
         "contentDetails": {"duration": "PT10M"}},
        {"id": "v1", "snippet": {"title": "Shorts", "publishedAt": "2026-10-02T00:00:00Z"},
         "statistics": {"viewCount": "30000"}, "contentDetails": {"duration": "PT45S"}},
        {"id": "v2", "snippet": {"title": "Стрим", "publishedAt": "2026-10-03T00:00:00Z",
                                 "liveBroadcastContent": "live"},
         "statistics": {}, "contentDetails": {"duration": "P0D"}},
    ]}
    return {"/channels": FakeResponse(200, channel), "/playlistItems": FakeResponse(200, playlist),
            "/videos": FakeResponse(200, videos)}


def test_youtube_api():
    session = FakeSession(_yt_routes())
    collector = YouTubeCollector(Settings(yt_api_key="key"), session=session)
    data = collector.collect(parse_link("youtube.com/@masr"))
    assert data.ok and data.source == "youtube_api"
    assert data.followers == 4200 and data.country == "EG"
    assert [i.id for i in data.items] == ["v0", "v1"]       # прямой эфир пропущен
    assert data.items[1].duration_sec == 45
    assert collector.quota_used == 3
    assert session.calls[0][1]["forHandle"] == "@masr"


def test_youtube_quota_falls_back_to_ytdlp():
    routes = {"/channels": FakeResponse(403, {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"channel": "Crypto Masr", "channel_follower_count": 4200, "uploader_id": "@masr",
                    "channel_url": "https://www.youtube.com/channel/UC123",
                    "entries": [{"id": "a", "title": "Видео", "view_count": 1200, "timestamp": 1790000000}]}

    collector = YouTubeCollector(Settings(yt_api_key="key"), session=FakeSession(routes), ytdlp_factory=FakeYDL)
    data = collector.collect(parse_link("youtube.com/@masr"))
    assert data.ok and data.source == "yt_dlp" and data.approximate
    assert any("YouTube API недоступен" in n for n in data.notes)
    assert data.items[0].views == 1200


def test_youtube_not_found():
    routes = {"/channels": FakeResponse(200, {"items": []})}
    data = YouTubeCollector(Settings(yt_api_key="key"), session=FakeSession(routes)).collect(parse_link("youtube.com/@nobody"))
    assert data.status == "not_found"


def test_parse_duration():
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("PT45S") == 45
    assert parse_duration("P0D") == 0
    assert parse_duration(None) is None


# --- X ------------------------------------------------------------------------

def test_x_without_token_is_unavailable_not_error():
    data = XCollector(Settings()).collect(parse_link("x.com/trader"))
    assert data.status == "unavailable"
    assert "X_BEARER_TOKEN" in data.status_reason


@pytest.mark.parametrize("status", [401, 403, 429])
def test_x_access_limits(status):
    session = FakeSession({"/users/by/username/": FakeResponse(status, {"title": "Forbidden"})})
    data = XCollector(Settings(x_bearer_token="t"), session=session).collect(parse_link("x.com/trader"))
    assert data.status == "unavailable"


def test_x_api_success():
    user = {"data": {"id": "1", "username": "trader", "name": "Trader", "location": "Cairo, Egypt",
                     "public_metrics": {"followers_count": 9000}, "created_at": "2019-01-01T00:00:00Z"}}
    tweets = {"data": [{"id": "10", "text": "BTC update", "created_at": "2026-10-04T00:00:00Z",
                        "public_metrics": {"impression_count": 1500, "like_count": 20, "retweet_count": 5,
                                           "reply_count": 3}}]}
    session = FakeSession({"/users/by/username/": FakeResponse(200, user), "/tweets": FakeResponse(200, tweets)})
    data = XCollector(Settings(x_bearer_token="t"), session=session).collect(parse_link("x.com/trader"))
    assert data.ok and data.followers == 9000 and data.location == "Cairo, Egypt"
    assert data.items[0].views == 1500 and data.items[0].reactions == 25


def test_x_tweets_forbidden_keeps_followers():
    user = {"data": {"id": "1", "username": "trader", "public_metrics": {"followers_count": 9000}}}
    session = FakeSession({"/users/by/username/": FakeResponse(200, user), "/tweets": FakeResponse(403, {})})
    data = XCollector(Settings(x_bearer_token="t"), session=session).collect(parse_link("x.com/trader"))
    assert data.ok and data.followers == 9000 and data.items == []
    assert "посты недоступны" in data.notes[0]
