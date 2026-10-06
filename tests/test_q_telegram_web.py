"""Telegram без входа: разбор публичных страниц t.me/<name> и t.me/s/<name>."""

from __future__ import annotations

from types import SimpleNamespace

from qualifier.config import Settings
from qualifier.links import parse_link
from qualifier.platforms.telegram import TelegramCollector
from qualifier.platforms.telegram_web import TelegramWebCollector, parse_count, parse_feed, parse_profile

PROFILE = """<div class="tgme_page_title" dir="auto"><span dir="auto">Crypto Masr</span></div>
<div class="tgme_page_extra">13 556 subscribers</div>
<div class="tgme_page_description" dir="auto">Канал про крипту.<br/>По рекламе: <a href="https://t.me/masr_ads">@masr_ads</a></div>"""

GROUP = """<div class="tgme_page_title"><span>Masr Chat</span></div>
<div class="tgme_page_extra">2 410 members, 37 online</div>"""

USER = """<div class="tgme_page_title"><span>Some Person</span></div><div class="tgme_page_extra">@someone</div>"""


def _post(pid: int, views: str, text: str, reactions: str = "") -> str:
    return (
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
        f'data-post="masr/{pid}"><div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>'
        f'<div class="tgme_widget_message_reactions js-message_reactions">{reactions}</div>'
        f'<span class="tgme_widget_message_views">{views}</span>'
        f'<time datetime="2026-10-0{pid % 9 + 1}T10:00:00+00:00" class="time">10:00</time></div></div>'
    )


FEED_1 = (
    '<a class="tme_messages_more" data-before="98" href="/s/masr?before=98"></a>'
    + _post(99, "1.8K", "Разбор рынка<br/>BTC &amp; ETH",
            '<span class="tgme_reaction tgme_reaction_paid"><i></i>5K</span>'
            '<span class="tgme_reaction"><tg-emoji></tg-emoji>41</span>'
            '<span class="tgme_reaction"><b>👍</b>1.2K</span>')
    + _post(100, "2 345", "Пост два")
)
FEED_2 = _post(97, "950", "Старый пост") + _post(98, "1,1K", "Ещё пост")


def test_parse_count():
    assert parse_count("10 498 219") == 10498219
    assert parse_count("18.9M") == 18_900_000
    assert parse_count("6.29K") == 6290
    assert parse_count("1,1K") == 1100
    assert parse_count("") is None


def test_parse_profile():
    channel = parse_profile(PROFILE)
    assert channel == {"title": "Crypto Masr", "description": "Канал про крипту.\nПо рекламе: @masr_ads",
                       "kind": "channel", "count": 13556, "extra": "13 556 subscribers"}
    group = parse_profile(GROUP)
    assert group["kind"] == "group" and group["count"] == 2410
    assert parse_profile(USER)["kind"] is None


def test_parse_feed():
    items, before = parse_feed(FEED_1, "masr")
    assert before == 98
    first = items[0]
    assert first.id == "99" and first.views == 1800
    assert first.text == "Разбор рынка\nBTC & ETH"
    assert first.reactions == 41 + 1200            # платные звёзды не считаем реакциями
    assert first.url == "https://t.me/masr/99"
    assert items[1].views == 2345 and items[1].reactions == 0


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None:
            return SimpleNamespace(status_code=404, text="", url=url)
        return SimpleNamespace(status_code=200, text=body, url=url)


def _collector(pages, **settings):
    s = Settings(rate_min_interval=0, rate_max_interval=0, **settings)
    return TelegramWebCollector(s, session=FakeSession(pages), sleeper=lambda _x: None)


def test_collect_channel_with_pagination():
    pages = {
        "https://t.me/masr": PROFILE,
        "https://t.me/s/masr": FEED_1,
        "https://t.me/s/masr?before=98": FEED_2,
    }
    data = _collector(pages).collect(parse_link("t.me/masr"))
    assert data.ok and data.source == "telegram_web"
    assert data.followers == 13556 and data.title == "Crypto Masr"
    assert {i.id for i in data.items} == {"97", "98", "99", "100"}
    assert data.has_community_chat is None            # без входа чат обсуждений не виден
    assert any("округлены" in n for n in data.notes)


def test_collect_group_and_user():
    data = _collector({"https://t.me/masr": GROUP}).collect(parse_link("t.me/masr"))
    assert data.kind == "group" and data.community_members == 2410
    data = _collector({"https://t.me/masr": USER}).collect(parse_link("t.me/masr"))
    assert data.status == "unavailable"
    data = _collector({"https://t.me/masr": "<html></html>"}).collect(parse_link("t.me/masr"))
    assert data.status == "not_found"


def test_feed_closed():
    data = _collector({"https://t.me/masr": PROFILE}).collect(parse_link("t.me/masr"))
    assert data.ok and data.items == []
    assert any("веб-превью" in n for n in data.notes)


def test_posts_limit():
    pages = {"https://t.me/masr": PROFILE, "https://t.me/s/masr": FEED_1,
             "https://t.me/s/masr?before=98": FEED_2}
    data = _collector(pages, tg_posts_limit=3).collect(parse_link("t.me/masr"))
    assert len(data.items) == 3


def test_telegram_collector_falls_back_without_session():
    web = SimpleNamespace(collect=lambda ref: f"web:{ref.handle}")
    collector = TelegramCollector(Settings(), gateway_factory=lambda: None, web_fallback=web)
    assert collector.collect(parse_link("t.me/masr")) == "web:masr"
