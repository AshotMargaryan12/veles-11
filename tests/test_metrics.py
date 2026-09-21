"""Метрики канала (раздел 4 ТЗ)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import NOW, make_posts, make_snapshot
from tghunter.metrics import collect_metrics
from tghunter.models import Post


def metrics_for(snapshot, blacklist=None, stream="test"):
    return collect_metrics(snapshot, blacklist_markers=blacklist, stream=stream, now=NOW)


def test_basic_metrics():
    m = metrics_for(make_snapshot())
    assert m["username"] == "good_channel"
    assert m["subscribers"] == 10000
    assert m["link"] == "https://t.me/good_channel"
    assert m["last_post_date"].startswith("2026-09-21")


def test_er_uses_average_views_over_subscribers():
    snapshot = make_snapshot(subscribers=10000, posts=make_posts(count=10, views=800))
    m = metrics_for(snapshot)
    assert m["er"] == pytest.approx(8.0)
    assert m["median_views"] == 800


def test_er_ignores_posts_older_than_60_days():
    """Пост старше 60 дней не должен тянуть ER вверх (раздел 4.2 ТЗ)."""
    posts = make_posts(count=10, views=500)
    posts.append(Post(id=999, date=NOW - timedelta(days=200), text="вирус", views=10**6))
    m = metrics_for(make_snapshot(subscribers=10000, posts=posts))
    assert m["er"] == pytest.approx(5.0)
    assert m["er_sample_size"] == 10


def test_er_uses_only_last_10_posts():
    posts = make_posts(count=10, views=1000)
    posts += make_posts(count=10, views=1, start_days_ago=25)
    m = metrics_for(make_snapshot(subscribers=10000, posts=posts))
    assert m["er"] == pytest.approx(10.0)


def test_median_views_is_robust_to_viral_outlier():
    posts = make_posts(count=9, views=1000)
    posts.insert(0, Post(id=500, date=NOW, text="вирус", views=100000))
    m = metrics_for(make_snapshot(subscribers=10000, posts=posts))
    assert m["median_views"] == 1000
    assert m["avg_views"] > m["median_views"]


def test_liveness_counts_days_and_frequency():
    posts = make_posts(count=20, step_days=3, start_days_ago=5)
    m = metrics_for(make_snapshot(posts=posts))
    assert m["days_since_last_post"] == 5
    assert m["posts_last_30d"] == 9


def test_empty_channel_has_no_liveness():
    m = metrics_for(make_snapshot(posts=[]))
    assert m["days_since_last_post"] is None
    assert m["posts_last_30d"] == 0
    assert m["er"] == 0.0


def test_comments_and_reactions():
    m = metrics_for(make_snapshot(has_linked_chat=True, posts=make_posts(reactions=25)))
    assert m["comments_enabled"] is True
    assert m["avg_reactions"] == 25.0

    m = metrics_for(make_snapshot(has_linked_chat=False))
    assert m["comments_enabled"] is False


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Рынок акций и деньги, это важно для инвесторов", "ru"),
        ("Ринок акцій сьогодні зріс, це важливо для інвесторів", "uk"),
        ("The crypto market and trading volume for you", "en"),
        ("Қаржы нарығы және ақша туралы", "turkic"),
        ("Moliyaviy bozor va pul uchun", "turkic"),
    ],
)
def test_language_detection(text, expected):
    snapshot = make_snapshot(title="", about="", posts=make_posts(text=text))
    assert metrics_for(snapshot)["language"] == expected


def test_monetization_detected_from_ad_markers():
    posts = make_posts(text="Отличный сервис #реклама erid: 2Vfnxw")
    m = metrics_for(make_snapshot(posts=posts))
    assert m["monetized"] is True
    assert "#реклама" in m["monetization_markers"]
    assert "erid" in m["monetization_markers"]


def test_monetization_detected_from_referral_links():
    posts = make_posts(text="Регистрация https://binance.com/ru?ref=ABC123")
    m = metrics_for(make_snapshot(posts=posts))
    assert m["monetized"] is True
    assert "ref=" in m["monetization_markers"]


def test_clean_channel_is_not_monetized():
    m = metrics_for(make_snapshot(about="Просто канал", posts=make_posts(text="Обзор рынка")))
    assert m["monetized"] is False


def test_contact_extracted_from_description():
    m = metrics_for(make_snapshot(about="Инвестиции. По рекламе: @adv_manager"))
    assert m["contact"] == "@adv_manager"
    assert m["has_contact"] is True


def test_no_contact_is_flagged():
    m = metrics_for(make_snapshot(about="Просто канал об инвестициях"))
    assert m["contact"] is None
    assert m["has_contact"] is False


def test_blacklist_hit_records_marker():
    snapshot = make_snapshot(title="Памп сигналы x100", about="")
    m = metrics_for(snapshot, blacklist=["памп", "x100", "казино"])
    assert m["blacklist_hit"] is True
    assert set(m["blacklist_markers"]) == {"памп", "x100"}


def test_source_is_preserved():
    snapshot = make_snapshot(method="similar", source_channel="@seed_partner")
    m = metrics_for(snapshot, stream="neighbors")
    assert m["method"] == "similar"
    assert m["source_channel"] == "@seed_partner"
    assert m["stream"] == "neighbors"


def test_provider_failure_does_not_break_collection(monkeypatch):
    """Падение одного провайдера не должно ронять сбор метрик."""
    import tghunter.metrics as metrics_module

    def boom(_snapshot, _ctx):
        raise ValueError("провайдер сломался")

    original = list(metrics_module._REGISTRY)
    metrics_module._REGISTRY.append(("broken", boom))
    try:
        m = metrics_for(make_snapshot())
        assert m["username"] == "good_channel"
        assert any("broken" in e for e in m["metrics_errors"])
    finally:
        metrics_module._REGISTRY[:] = original


def test_custom_provider_extends_metrics_dict():
    """Раздел 13 ТЗ: новый модуль метрик добавляется без правок остального кода."""
    import tghunter.metrics as metrics_module

    original = list(metrics_module._REGISTRY)
    metrics_module.register_provider(
        "topic_classifier", lambda s, ctx: {"topic": "crypto", "quality": 0.9}
    )
    try:
        m = metrics_for(make_snapshot())
        assert m["topic"] == "crypto"
        assert m["quality"] == 0.9
    finally:
        metrics_module._REGISTRY[:] = original
