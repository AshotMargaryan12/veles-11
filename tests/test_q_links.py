"""Разбор ссылок партнёра (раздел 3 ТЗ)."""

from __future__ import annotations

import pytest

from qualifier.links import LinkError, parse_batch_line, parse_link, parse_links
from qualifier.models import PartnerRequest


@pytest.mark.parametrize(
    "raw, handle",
    [
        ("t.me/crypto_masr", "crypto_masr"),
        ("https://t.me/crypto_masr", "crypto_masr"),
        ("@crypto_masr", "crypto_masr"),
        ("https://t.me/s/crypto_masr", "crypto_masr"),
        ("https://t.me/crypto_masr/1234", "crypto_masr"),
        ("telegram.me/crypto_masr", "crypto_masr"),
        ("tg://resolve?domain=crypto_masr", "crypto_masr"),
        ("crypto_masr", "crypto_masr"),
    ],
)
def test_telegram_links(raw, handle):
    ref = parse_link(raw)
    assert ref.platform == "telegram"
    assert ref.handle == handle
    assert ref.url == f"https://t.me/{handle}"


@pytest.mark.parametrize("raw", ["t.me/+AbCdEf123", "https://t.me/joinchat/xyz", "t.me/addlist/abc"])
def test_private_telegram_links_rejected(raw):
    with pytest.raises(LinkError, match="приватная"):
        parse_link(raw)


@pytest.mark.parametrize(
    "raw, kind, handle",
    [
        ("youtube.com/@CoinBureau", "handle", "CoinBureau"),
        ("https://www.youtube.com/@CoinBureau/videos", "handle", "CoinBureau"),
        ("https://m.youtube.com/channel/UCqK_GSMbpiV8spgD3ZGloSw", "channel_id", "UCqK_GSMbpiV8spgD3ZGloSw"),
        ("https://www.youtube.com/user/oldname", "user", "oldname"),
        ("https://www.youtube.com/c/customname", "custom", "customname"),
        ("https://youtu.be/dQw4w9WgXcQ", "video", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "video", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abc123", "video", "abc123"),
    ],
)
def test_youtube_links(raw, kind, handle):
    ref = parse_link(raw)
    assert ref.platform == "youtube"
    assert ref.kind == kind
    assert ref.handle == handle


@pytest.mark.parametrize(
    "raw", ["x.com/trader", "https://twitter.com/trader", "https://x.com/trader/status/123", "mobile.twitter.com/trader"]
)
def test_x_links(raw):
    ref = parse_link(raw)
    assert ref.platform == "x"
    assert ref.handle == "trader"
    assert ref.url == "https://x.com/trader"


@pytest.mark.parametrize("raw", ["https://example.com/page", "x.com/home", "https://youtube.com/", ""])
def test_unsupported_links(raw):
    with pytest.raises(LinkError):
        parse_link(raw)


def test_multiple_links_dedup_and_split():
    refs = parse_links(["t.me/abcde, youtube.com/@abcde", "@abcde x.com/abcde"])
    assert [(r.platform, r.handle) for r in refs] == [
        ("telegram", "abcde"), ("youtube", "abcde"), ("x", "abcde"),
    ]


def test_batch_line_with_options():
    request = parse_batch_line(
        't.me/abcde youtube.com/@abcde geo=EG type=institutional notes="met at conf"',
        PartnerRequest(links=[], refresh=True),
    )
    assert request.links == ["t.me/abcde", "youtube.com/@abcde"]
    assert request.geo == "EG"
    assert request.affiliate_type == "institutional"
    assert request.notes == "met at conf"
    assert request.refresh is True


def test_batch_line_comments_and_defaults():
    assert parse_batch_line("   # комментарий") is None
    assert parse_batch_line("") is None
    request = parse_batch_line("t.me/abcde", PartnerRequest(links=[], geo="RU"))
    assert request.geo == "RU"
    assert request.affiliate_type == "individual"
