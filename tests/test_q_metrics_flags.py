"""Метрики площадки, реклама и флаги риска (разделы 4.1, 6 ТЗ)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from qfixtures import QNOW, items, tg_data
from qualifier.ads import find_contact, scan_post
from qualifier.config import load_thresholds
from qualifier.flags import compute_flags, keyword_share
from qualifier.models import (
    INSUFFICIENT,
    Assessed,
    AudienceAssessment,
    ContentItem,
    PlatformData,
    RedFlag,
    platform_key,
)
from qualifier.stats import compute_metrics, merge_albums, primary_key

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def th():
    return load_thresholds(REPO / "config" / "qualifier")


def test_er_uses_median_and_ignores_viral_post(th):
    views = [1000] * 19 + [50000]
    data = tg_data(followers=10000, posts=items(20, views_list=views))
    m = compute_metrics(data, th, QNOW)
    assert m.median_views == 1000
    assert m.er == 10.0          # медиана / подписчики
    assert m.er_avg > 30         # среднее раздуто вирусным постом
    assert m.max_to_median == 50.0


def test_young_posts_excluded_from_views(th):
    posts = items(10, views=2000, start_hours=48)
    posts.insert(0, ContentItem(id="new", date=QNOW - timedelta(hours=2), text="свежий", views=100))
    m = compute_metrics(tg_data(followers=10000, posts=posts), th, QNOW)
    assert m.young_posts_excluded == 1
    assert m.median_views == 2000


def test_activity_window_and_cap(th):
    posts = items(50, step_hours=10, start_hours=1)  # все 50 постов за ~21 день
    m = compute_metrics(tg_data(posts=posts), th, QNOW)
    assert m.posts_30d == 50
    assert m.posts_30d_capped is True   # выгрузили лимит — постов может быть больше
    assert m.days_since_last_post == 0


def test_albums_are_merged():
    base = QNOW - timedelta(days=2)
    album = [
        ContentItem(id="1", date=base, text="", views=900, reactions=5, group_id="g"),
        ContentItem(id="2", date=base, text="подпись альбома", views=900, reactions=7, group_id="g"),
        ContentItem(id="3", date=base - timedelta(days=1), text="обычный пост", views=800),
    ]
    merged = merge_albums(album)
    assert len(merged) == 2
    assert merged[0].text == "подпись альбома"
    assert merged[0].reactions == 7


def test_ads_detection():
    assert scan_post("Реклама. ООО «Ромашка», erid: 2VtzqwXy").is_ad
    assert scan_post("#реклама Курс по трейдингу").is_ad
    assert scan_post("Регистрируйтесь: https://www.bybit.com/register?ref=ABC123").exchange_ref
    assert scan_post("Подробнее https://site.com/page?utm_source=tg").markers == ["utm"]
    # простая ссылка на биржу рекламой не считается
    assert not scan_post("Binance опубликовал отчёт https://www.binance.com/en/blog/123").is_ad
    assert not scan_post("Обычный пост про рынок без ссылок").is_ad


def test_ads_counted_in_metrics(th):
    texts = ["#реклама erid: abc Курс по трейдингу"] * 6 + ["Разбор рынка"] * 24
    posts = items(30, text=texts)
    m = compute_metrics(tg_data(posts=posts), th, QNOW)
    assert m.sells_ads
    assert m.ads_posts == 6
    assert "#реклама" in m.ads_markers and "erid" in m.ads_markers


def test_contact_extraction():
    assert find_contact("Канал про рынок. По рекламе: @adv_manager", "mychannel") == "@adv_manager"
    assert find_contact("Business: team@example.com") == "team@example.com"
    assert find_contact("Наш канал t.me/mychannel", "mychannel") is None


def _flags(data, audience=None, th=None):
    th = th or load_thresholds(REPO / "config" / "qualifier")
    metrics = {platform_key(data): compute_metrics(data, th, QNOW)}
    return {f.code: f for f in compute_flags([data], metrics, audience or AudienceAssessment(), th)}


def test_healthy_channel_has_no_flags():
    assert _flags(tg_data(followers=13556, posts=items(30, views=1800, reactions=40))) == {}


def test_low_er_thresholds_by_size():
    small = _flags(tg_data(followers=8000, posts=items(20, views=600, reactions=20)))   # 7.5% < 10%
    large = _flags(tg_data(followers=40000, posts=items(20, views=2400, reactions=20)))  # 6% >= 5%
    assert "low_er" in small
    assert "low_er" not in large


def test_views_spike():
    views = [1000] * 15 + [9000, 12000, 1000, 1000, 1000]
    flags = _flags(tg_data(followers=10000, posts=items(20, views_list=views, reactions=30)))
    assert "views_spike" in flags


def test_dead_subs_and_no_comments():
    data = tg_data(followers=42000, posts=items(20, views=600, reactions=None), linked=False)
    flags = _flags(data)
    assert flags["dead_subs"].severity == "critical"
    assert "no_comments" in flags
    assert "low_er" in flags


def test_dead_subs_not_raised_when_reactions_unknown_outside_telegram():
    data = PlatformData(
        platform="youtube", handle="big", url="https://www.youtube.com/@big", followers=2_000_000,
        items=items(10, views=8000, reactions=None), source="yt_dlp",
    )
    assert "dead_subs" not in _flags(data)


def test_inactive():
    flags = _flags(tg_data(posts=items(10, start_hours=24 * 40)))
    assert "inactive" in flags
    assert "40" in flags["inactive"].evidence


def test_airdrop_heavy_from_keywords_without_llm():
    texts = ["Новый аирдроп: делаем клейм поинтов в тестнете"] * 6 + ["Разбор рынка BTC"] * 4
    flags = _flags(tg_data(posts=items(10, text=texts)))
    assert flags["airdrop_heavy"].source == "keywords"


def test_airdrop_heavy_from_llm():
    audience = AudienceAssessment(
        available=True,
        audience_type=Assessed("airdrop_hunters", ["«клейм поинтов» [TG#1]"]),
        airdrop_share=Assessed("dominant", ["8 из 10 постов — айрдропы"]),
    )
    flags = _flags(tg_data(), audience)
    assert flags["airdrop_heavy"].source == "llm"
    assert "8 из 10" in flags["airdrop_heavy"].evidence


def test_brand_risk_severity_depends_on_flag_type():
    severe = AudienceAssessment(
        available=True,
        red_flags=[RedFlag("guaranteed_returns", "100% прибыль гарантирована", verified=True)],
    )
    mild = AudienceAssessment(
        available=True,
        red_flags=[RedFlag("get_rich_quick", "зарабатывай каждый день", verified=True)],
    )
    unverified = AudienceAssessment(
        available=True,
        red_flags=[RedFlag("pump", "выдуманная цитата", verified=False)],
    )
    assert _flags(tg_data(), severe)["brand_risk"].severity == "critical"
    assert _flags(tg_data(), mild)["brand_risk"].severity == "warn"
    flag = _flags(tg_data(), unverified)["brand_risk"]
    assert flag.severity == "warn"
    assert "цитата не найдена" in flag.evidence


def test_brand_risk_keywords_fallback():
    data = tg_data(description="Гарантированный доход каждый день! Пиши @admin_vip")
    flag = _flags(data)["brand_risk"]
    assert flag.source == "keywords"
    assert "Гарантированный" in flag.evidence


def test_keyword_share_word_start_for_latin():
    share, hits = keyword_share(["Airdrops are back", "Question of the day"], ["airdrop", "quest"])
    assert hits == 2  # prefix-совпадение: «quest» находит «Question» — поэтому его нет в словаре
    share, hits = keyword_share(["claimed rewards", "reclaim"], ["claim"])
    assert hits == 1


def test_primary_key_prefers_largest_channel():
    small = tg_data(handle="small_one", followers=1000)
    big = tg_data(handle="big_one", followers=50000)
    group = tg_data(handle="chat_one", followers=90000)
    group.kind = "group"
    assert primary_key([small, group, big]) == "telegram:big_one"
    assert primary_key([PlatformData(platform="x", handle="a", url="", status="unavailable")]) is None
    assert INSUFFICIENT == "insufficient_data"
