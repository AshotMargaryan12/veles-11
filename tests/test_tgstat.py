"""Источник TGStat: работает по API-ключу, без юзер-сессии."""

from __future__ import annotations

import pytest

from tghunter.metrics import collect_metrics
from tghunter.sources.tgstat import TGStatError, TGStatGateway


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttp:
    """Отдаёт заранее заданные ответы и записывает, о чём его спросили."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        method = url.rsplit("/api.tgstat.ru/", 1)[-1]
        self.calls.append((method, params or {}))
        payload = self.responses.get(method, {"status": "error", "error": "no mock"})
        return FakeResponse(payload)

    def close(self):
        pass


SEARCH_OK = {
    "status": "ok",
    "response": {
        "count": 2,
        "items": [
            {"id": 1, "tg_id": 1001, "username": "@found_one", "title": "Первый",
             "about": "Крипта", "participants_count": 15000},
            {"id": 2, "tg_id": 1002, "username": "found_two", "title": "Второй",
             "about": "Трейдинг", "participants_count": 9000},
        ],
    },
}

POSTS_OK = {
    "status": "ok",
    "response": {
        "count": 2,
        "channel": {"id": 1, "tg_id": 1001, "username": "found_one", "title": "Первый",
                    "about": "Крипта. По рекламе: @adv_man", "participants_count": 15000},
        "items": [
            {"id": 11, "date": 1790000000, "views": 3000,
             "text": "Обзор рынка #реклама erid: 2Vfn"},
            {"id": 12, "date": 1789900000, "views": 2800, "text": "Ещё обзор рынка"},
        ],
    },
}


def gateway(responses, **kwargs):
    return TGStatGateway("test_token", session=FakeHttp(responses), **kwargs)


def test_token_is_required():
    with pytest.raises(TGStatError, match="TGSTAT_TOKEN"):
        TGStatGateway("")


def test_token_travels_in_every_request():
    gw = gateway({"channels/search": SEARCH_OK})
    gw.search_keyword("крипта")
    assert all(params.get("token") == "test_token" for _m, params in gw.http.calls)


def test_search_returns_candidates():
    gw = gateway({"channels/search": SEARCH_OK})
    found = gw.search_keyword("крипта", limit=10)

    assert [c.username for c in found] == ["found_one", "found_two"]  # @ срезается
    assert all(c.method == "keywords" for c in found)
    assert found[0].channel_id == 1001
    method, params = gw.http.calls[0]
    assert method == "channels/search"
    assert params["q"] == "крипта"
    assert params["peer_type"] == "channel"


def test_search_limit_is_capped_at_api_maximum():
    gw = gateway({"channels/search": SEARCH_OK})
    gw.search_keyword("крипта", limit=500)
    assert gw.http.calls[0][1]["limit"] == 100


def test_short_query_is_not_sent():
    """TGStat требует минимум 3 символа — не тратим запрос впустую."""
    gw = gateway({"channels/search": SEARCH_OK})
    assert gw.search_keyword("др") == []
    assert gw.http.calls == []


def test_channel_without_username_is_skipped():
    payload = {"status": "ok", "response": {"items": [
        {"id": 3, "tg_id": 1003, "username": "", "title": "Приватный"}]}}
    assert gateway({"channels/search": payload}).search_keyword("крипта") == []


def test_snapshot_is_built_from_posts_and_channel():
    gw = gateway({"channels/posts": POSTS_OK})
    snapshot = gw.fetch_snapshot("found_one", posts_limit=20)

    assert snapshot is not None
    assert snapshot.username == "found_one"
    assert snapshot.subscribers == 15000
    assert len(snapshot.posts) == 2
    assert snapshot.posts[0].views == 3000
    method, params = gw.http.calls[0]
    assert method == "channels/posts"
    assert params["channelId"] == "@found_one"
    assert params["extended"] == 1


def test_posts_limit_is_capped_at_api_maximum():
    gw = gateway({"channels/posts": POSTS_OK})
    gw.fetch_snapshot("found_one", posts_limit=500)
    assert gw.http.calls[0][1]["limit"] == 50


def test_snapshot_feeds_the_normal_metrics_pipeline():
    """Данные TGStat проходят через тот же слой метрик, что и MTProto."""
    snapshot = gateway({"channels/posts": POSTS_OK}).fetch_snapshot("found_one")
    metrics = collect_metrics(snapshot, blacklist_markers=["казино"], stream="test")

    assert metrics["subscribers"] == 15000
    assert metrics["monetized"] is True
    assert metrics["contact"] == "@adv_man"
    assert metrics["er"] > 0


def test_missing_subscribers_are_fetched_from_stat():
    posts = {"status": "ok", "response": {
        "channel": {"tg_id": 1001, "username": "found_one", "title": "Первый"},
        "items": [{"id": 1, "date": 1790000000, "views": 100, "text": "пост"}]}}
    stat = {"status": "ok", "response": {"participants_count": 7777}}
    gw = gateway({"channels/posts": posts, "channels/stat": stat})

    assert gw.fetch_snapshot("found_one").subscribers == 7777
    assert [m for m, _p in gw.http.calls] == ["channels/posts", "channels/stat"]


def test_bad_token_raises_clearly():
    payload = {"status": "error", "error": "Invalid token"}
    with pytest.raises(TGStatError, match="отверг токен"):
        gateway({"channels/search": payload}).search_keyword("крипта")


def test_api_error_returns_empty_not_crash():
    payload = {"status": "error", "error": "limit reached"}
    assert gateway({"channels/search": payload}).search_keyword("крипта") == []


def test_http_error_returns_empty():
    class Broken:
        calls: list = []

        def get(self, *a, **kw):
            raise ConnectionError("сеть упала")

    gw = TGStatGateway("tok", session=Broken())
    assert gw.search_keyword("крипта") == []
    assert gw.fetch_snapshot("some_chan") is None


def test_check_reports_success():
    ok, message = gateway({"channels/search": SEARCH_OK}).check()
    assert ok and "принят" in message


def test_check_reports_bad_token():
    ok, message = gateway({"channels/search": {"status": "error", "error": "Invalid token"}}).check()
    assert not ok and "токен" in message


def test_unavailable_methods_return_empty_without_requests():
    """similar и папки — только через MTProto; TGStat молча отдаёт пусто."""
    gw = gateway({})
    assert gw.similar_channels("any") == []
    assert gw.folder_channels("https://t.me/addlist/X") == []
    assert gw.http.calls == []


def test_post_without_date_is_dropped():
    payload = {"status": "ok", "response": {
        "channel": {"tg_id": 1, "username": "c", "participants_count": 10},
        "items": [{"id": 1, "views": 5, "text": "без даты"},
                  {"id": 2, "date": 1790000000, "views": 5, "text": "с датой"}]}}
    snapshot = gateway({"channels/posts": payload}).fetch_snapshot("c")
    assert len(snapshot.posts) == 1
