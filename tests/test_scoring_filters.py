"""Скоринг и фильтры (раздел 5 ТЗ)."""

from __future__ import annotations

import pytest

from tghunter.config import Stream
from tghunter.filters import check_filters
from tghunter.scoring import MAX_SCORE, format_breakdown, score_channel


def base_metrics(**overrides):
    metrics = {
        "subscribers": 10000,
        "er": 5.0,
        "avg_reactions": 10.0,
        "comments_enabled": True,
        "days_since_last_post": 1,
        "posts_last_30d": 10,
        "language": "ru",
        "monetized": False,
        "contact": "@adv",
        "has_contact": True,
        "blacklist_hit": False,
        "method": "keywords",
    }
    metrics.update(overrides)
    return metrics


def test_max_score_is_100():
    assert MAX_SCORE == 100


def test_perfect_channel_scores_100():
    score, breakdown = score_channel(
        base_metrics(monetized=True, method="similar", er=9.0)
    )
    assert score == 100
    assert breakdown == {
        "monetized": 30,
        "similar": 25,
        "high_er": 20,
        "contact": 10,
        "live_comments": 10,
        "frequency": 5,
    }


@pytest.mark.parametrize(
    "overrides, key, weight",
    [
        ({"monetized": True}, "monetized", 30),
        ({"method": "similar"}, "similar", 25),
        ({"er": 7.1}, "high_er", 20),
    ],
)
def test_individual_weights(overrides, key, weight):
    plain = base_metrics(contact=None, has_contact=False, comments_enabled=False,
                         posts_last_30d=1)
    assert score_channel(plain)[0] == 0
    score, breakdown = score_channel({**plain, **overrides})
    assert score == weight
    assert breakdown[key] == weight


def test_er_exactly_at_threshold_does_not_score():
    """+20 даётся за ER ВЫШЕ 7%, ровно 7% не считается."""
    assert "high_er" not in score_channel(base_metrics(er=7.0))[1]
    assert "high_er" in score_channel(base_metrics(er=7.01))[1]


def test_comments_without_reactions_do_not_score():
    breakdown = score_channel(base_metrics(comments_enabled=True, avg_reactions=2))[1]
    assert "live_comments" not in breakdown


def test_frequency_threshold():
    assert "frequency" not in score_channel(base_metrics(posts_last_30d=7))[1]
    assert "frequency" in score_channel(base_metrics(posts_last_30d=8))[1]


def test_format_breakdown_is_readable():
    assert format_breakdown({"monetized": 30, "similar": 25}) == "monetized+30|similar+25"


# --- фильтры ---------------------------------------------------------------

def stream(**overrides):
    defaults = dict(
        name="test", min_subscribers=1000, max_subscribers=100000, min_er=3.0,
        min_avg_reactions=5.0, max_days_since_post=14, min_posts_30d=4,
        languages=["ru"],
    )
    defaults.update(overrides)
    return Stream(**defaults)


def test_good_channel_passes():
    passed, reasons = check_filters(base_metrics(), stream())
    assert passed and reasons == []


def test_blacklist_short_circuits_everything():
    metrics = base_metrics(blacklist_hit=True, blacklist_markers=["памп"])
    passed, reasons = check_filters(metrics, stream())
    assert not passed
    assert reasons == ["blacklist_hit: памп"]


@pytest.mark.parametrize("subs", [999, 100001])
def test_subscriber_bounds(subs):
    passed, reasons = check_filters(base_metrics(subscribers=subs), stream())
    assert not passed
    assert "subscribers" in reasons[0]


def test_low_er_passes_when_comments_are_alive():
    """ER >= 3% ИЛИ (комментарии И реакции >= 5) — второе условие спасает канал."""
    metrics = base_metrics(er=1.0, comments_enabled=True, avg_reactions=6)
    assert check_filters(metrics, stream())[0]


def test_low_er_fails_without_live_comments():
    metrics = base_metrics(er=1.0, comments_enabled=False, avg_reactions=0)
    passed, reasons = check_filters(metrics, stream())
    assert not passed
    assert any("er" in r for r in reasons)


def test_dead_channel_fails_liveness():
    metrics = base_metrics(days_since_last_post=40, posts_last_30d=1)
    passed, reasons = check_filters(metrics, stream())
    assert not passed
    assert any("последний пост" in r for r in reasons)
    assert any("постов за 30 дней" in r for r in reasons)


def test_channel_without_posts_fails():
    metrics = base_metrics(days_since_last_post=None, posts_last_30d=0, er=0)
    passed, reasons = check_filters(metrics, stream())
    assert not passed
    assert "нет постов" in reasons


def test_wrong_language_fails():
    passed, reasons = check_filters(base_metrics(language="en"), stream())
    assert not passed
    assert any("language" in r for r in reasons)


def test_longlist_mode_skips_liveness_and_er():
    """regional работает в режиме «только лонглист» — пороги живости не применяются."""
    metrics = base_metrics(er=0.1, comments_enabled=False, avg_reactions=0,
                           days_since_last_post=90, posts_last_30d=0)
    passed, _ = check_filters(metrics, stream(mode="longlist"))
    assert passed


def test_longlist_mode_still_honours_blacklist():
    metrics = base_metrics(blacklist_hit=True, blacklist_markers=["казино"])
    assert not check_filters(metrics, stream(mode="longlist"))[0]


def test_longlist_mode_still_honours_subscribers():
    assert not check_filters(base_metrics(subscribers=50), stream(mode="longlist"))[0]
