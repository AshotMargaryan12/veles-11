"""Квалификация по criteria.yaml, CPA по гео и рекомендация (раздел 7 ТЗ)."""

from __future__ import annotations

import pytest

from qfixtures import SPEC_CRITERIA, make_config_dir, tg_data
from qualifier.config import ConfigError, load_cpa, load_criteria
from qualifier.models import (
    Assessed,
    AudienceAssessment,
    Flag,
    GeoEstimate,
    PlatformData,
)
from qualifier.qualify import qualify, recommend


@pytest.fixture
def cfg(tmp_path):
    config_dir = make_config_dir(tmp_path)
    criteria = load_criteria(config_dir)
    return criteria, load_cpa(config_dir, criteria.cpa_rates_file)


def geo(country="EG", confidence="medium", region="MENA"):
    return GeoEstimate(country=country, country_name=country, region=region,
                       confidence=confidence, source="auto")


def test_spec_example_tier_gap_and_cpa(cfg):
    criteria, cpa = cfg
    yt = PlatformData(platform="youtube", handle="x", url="", followers=4200)
    result = qualify([tg_data(followers=13556, members=1200), yt], criteria, cpa, geo())
    assert result.tier_rate == 40
    assert result.tier_met_by == ["followers_single_platform"]
    assert result.followers_platform == "telegram"
    assert result.next_tier_rate == 50
    gaps = {g.metric: g.missing for g in result.next_tier_gaps}
    assert gaps["followers_single_platform"] == 1444     # как в примере карточки ТЗ
    assert gaps["community_members"] == 8800
    assert (result.cpa, result.cpa_currency, result.cpa_event) == (11, "USD", "FTT")
    assert not result.criteria_are_examples


def test_followers_are_not_summed_across_platforms(cfg):
    criteria, cpa = cfg
    a = tg_data(handle="aaaaa", followers=9000)
    b = PlatformData(platform="youtube", handle="b", url="", followers=9000)
    result = qualify([a, b], criteria, cpa, geo())
    assert result.tier_rate == 30  # 9 000 на одной площадке, а не 18 000 в сумме


def test_community_members_can_qualify(cfg):
    criteria, cpa = cfg
    result = qualify([tg_data(followers=4000, members=8500)], criteria, cpa, geo())
    assert result.tier_rate == 40
    assert result.tier_met_by == ["community_members"]
    assert "чат обсуждений" in result.community_source


def test_institutional_type(cfg):
    criteria, cpa = cfg
    result = qualify([tg_data(followers=13556)], criteria, cpa, geo(), "institutional")
    assert result.tier_rate == 30
    assert result.next_tier_rate == 40


def test_unknown_affiliate_type(cfg):
    criteria, cpa = cfg
    with pytest.raises(ConfigError, match="не описан"):
        qualify([tg_data()], criteria, cpa, geo(), "vip")


def test_below_minimum_tier(cfg):
    criteria, cpa = cfg
    result = qualify([tg_data(followers=2000, members=100)], criteria, cpa, geo())
    assert result.tier_rate is None
    assert result.next_tier_rate == 30
    assert {g.metric: g.missing for g in result.next_tier_gaps}["followers_single_platform"] == 3000


def test_borderline_requires_manual_review(cfg):
    criteria, cpa = cfg
    near_next = qualify([tg_data(followers=14200)], criteria, cpa, geo(confidence="high"))
    assert near_next.manual_review
    assert any("между порогами" in r for r in near_next.manual_review_reasons)
    just_made = qualify([tg_data(followers=10300)], criteria, cpa, geo(confidence="high"))
    assert any("впритык" in r for r in just_made.manual_review_reasons)
    clear = qualify([tg_data(followers=12500, members=100)], criteria, cpa, geo(confidence="high"))
    assert not clear.manual_review


def test_low_geo_confidence_requires_manual_review(cfg):
    criteria, cpa = cfg
    result = qualify([tg_data(followers=12500, members=100)], criteria, cpa, geo(confidence="low"))
    assert "гео определено с низкой уверенностью" in result.manual_review_reasons


def test_cpa_regional_fallback_and_missing(cfg):
    criteria, cpa = cfg
    regional = qualify([tg_data(followers=12500)], criteria, cpa, geo("SA", "high", "MENA"))
    assert regional.cpa == 6 and regional.cpa_matched_by == "region"
    missing = qualify([tg_data(followers=12500)], criteria, cpa, geo("BR", "high", "LATAM"))
    assert missing.cpa is None
    assert any("нет CPA-ставки" in r for r in missing.manual_review_reasons)


def test_match_all(tmp_path):
    config_dir = make_config_dir(tmp_path, SPEC_CRITERIA.replace("individual:\n    tiers:", "individual:\n    match: all\n    tiers:"))
    criteria = load_criteria(config_dir)
    cpa = load_cpa(config_dir)
    result = qualify([tg_data(followers=16000, members=500)], criteria, cpa, geo())
    assert result.tier_rate is None  # подписчиков хватает, комьюнити — нет


def test_example_criteria_are_flagged(tmp_path):
    import shutil
    from pathlib import Path

    repo_config = Path(__file__).resolve().parents[1] / "config" / "qualifier"
    for name in ("criteria.example.yaml", "cpa_by_country.example.csv"):
        shutil.copy(repo_config / name, tmp_path / name)
    criteria = load_criteria(tmp_path)   # реального criteria.yaml нет — берётся пример
    assert criteria.is_example
    cpa = load_cpa(tmp_path)
    assert cpa.is_example
    result = qualify([tg_data(followers=13556)], criteria, cpa, geo())
    assert result.criteria_are_examples
    assert any("примеры-заглушки" in r for r in result.manual_review_reasons)


@pytest.mark.parametrize(
    "bad, message",
    [
        ("affiliate_types: {}", "affiliate_types"),
        ("affiliate_types:\n  individual:\n    tiers:\n      - rate: 50\n        subscribers: 10", "неизвестные условия"),
        ("affiliate_types:\n  individual:\n    tiers:\n      - followers_single_platform: 10", "нет rate"),
        ("affiliate_types:\n  individual:\n    match: some\n    tiers: []", "any или all"),
    ],
)
def test_criteria_validation(tmp_path, bad, message):
    config_dir = make_config_dir(tmp_path, bad)
    with pytest.raises(ConfigError, match=message):
        load_criteria(config_dir)


# --- рекомендация -----------------------------------------------------------

def _audience(conversion="medium", audience="traders"):
    return AudienceAssessment(
        available=True,
        audience_type=Assessed(audience, ["x"]),
        conversion=Assessed(conversion, ["x"]),
    )


def _rec(cfg, followers=12500, flags=(), audience=None, g=None):
    criteria, cpa = cfg
    g = g or geo(confidence="high")
    q = qualify([tg_data(followers=followers, members=100)], criteria, cpa, g)
    return recommend(q, list(flags), audience or _audience(), g, criteria)


def test_recommendation_airdrop_matches_spec(cfg):
    lines, deal = _rec(cfg, flags=[Flag("airdrop_heavy", "x")], audience=_audience("low", "airdrop_hunters"))
    assert lines[0].startswith("Старт на 40% на тестовый период")
    assert "без CPA и фикса" in lines[0]
    assert "доля приступивших к торговле" in lines[0] or "долю приступивших к торговле" in lines[0]
    assert deal.submit and not deal.cpa_included


def test_recommendation_blocked_by_critical_flag(cfg):
    lines, deal = _rec(cfg, flags=[Flag("dead_subs", "x", severity="critical")])
    assert lines[0].startswith("Не подавать на повышение до ручной проверки (dead_subs)")
    assert not deal.submit


def test_recommendation_high_conversion_with_cpa(cfg):
    lines, deal = _rec(cfg, audience=_audience("high"), g=geo("EG", "high"))
    assert lines[0].startswith("Подавать на 40%")
    assert "CPA" in lines[0]
    assert deal.cpa_included


def test_recommendation_below_tier(cfg):
    lines, deal = _rec(cfg, followers=1000)
    assert "ниже минимального тира" in lines[0]
    assert not deal.submit


def test_recommendation_geo_confirmation(cfg):
    lines, _ = _rec(cfg, g=geo(confidence="medium"))
    assert any("подтвердить гео" in line for line in lines)


def test_recommendation_without_data(cfg):
    criteria, cpa = cfg
    empty = PlatformData(platform="x", handle="a", url="", status="unavailable", status_reason="нет токена")
    q = qualify([empty], criteria, cpa, GeoEstimate())
    lines, deal = recommend(q, [], AudienceAssessment(), GeoEstimate(), criteria)
    assert lines[0].startswith("Данных по площадкам нет")
    assert not deal.submit
