"""Объёмы партнёра: автооценка, whitelisting (критерий 1), ROI, ввод и рекомендация.

Критерии — volume_criteria.example.yaml: все числа там вымышленные.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qfixtures import (
    QNOW,
    FakeAnthropic,
    StaticCollector,
    assessment,
    make_config,
    make_config_dir,
    make_settings,
    tg_data,
)
from qualifier.config import load_volume_criteria
from qualifier.links import LinkError, parse_batch_line
from qualifier.llm import LLMClient
from qualifier.models import PartnerRequest, PerformanceInput
from qualifier.performance import (
    compute_roi,
    evaluate,
    parse_money,
    performance_from_mapping,
)
from qualifier.render import card_summary, render_markdown
from qualifier.service import Qualifier
from qualifier.store import Store

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def vc(tmp_path_factory):
    import shutil

    folder = tmp_path_factory.mktemp("vc")
    shutil.copy(REPO / "config" / "qualifier" / "volume_criteria.example.yaml", folder)
    criteria = load_volume_criteria(folder)
    assert criteria.is_example
    return criteria


# --- автооценка и whitelisting ------------------------------------------------

def test_auto_evaluation_needs_no_whitelist(vc):
    m = evaluate(PerformanceInput(spot_volume=1_200_000, spot_new_traders=5), vc).markets["spot"]
    assert m.volume_period == 3_600_000 and m.new_traders_period == 15
    assert m.auto_rate_volume == 35 and m.auto_rate_full == 35
    assert m.auto_passed and not m.needs_whitelist


def test_whitelist_three_months(vc):
    # объём 110% порога, трейдеры 75% — правило 100/70 даёт тир на 3 месяца
    m = evaluate(PerformanceInput(spot_volume=1_100_000, spot_new_traders=3), vc).markets["spot"]
    assert m.auto_rate_full == 25
    assert m.needs_whitelist
    assert (m.whitelist_rate, m.whitelist_months) == (35, 3)
    assert "объём 110%" in m.whitelist_basis


def test_whitelist_one_month_with_ftt_and_alternative(vc):
    # новых трейдеров нет, их заменяют FTT; объём 90% и FTT 75% — правило 85/55, 1 месяц
    m = evaluate(PerformanceInput(spot_volume=900_000, ftt=3), vc).markets["spot"]
    assert m.ftt_used
    assert (m.whitelist_rate, m.whitelist_months) == (35, 1)
    assert m.alternative == (25, 3)


def test_top_ftt_rule(vc):
    perf = PerformanceInput(futures_volume=7_000_000, top_ftt_region=True)
    m = evaluate(perf, vc).markets["futures"]
    assert (m.whitelist_rate, m.whitelist_months) == (35, 3)
    assert "топ по FTT" in m.whitelist_basis
    m = evaluate(PerformanceInput(futures_volume=7_000_000), vc).markets["futures"]
    assert m.whitelist_rate is None
    assert m.auto_rate_volume == 35 and m.auto_rate_full == 8   # без трейдеров — ставка по умолчанию


def test_nothing_met(vc):
    result = evaluate(PerformanceInput(spot_volume=10_000, futures_volume=10_000), vc)
    assert not result.criteria1_met
    assert result.markets["spot"].next_gap.startswith("до 25%: не хватает объёма")


def test_notes_for_external_source(vc):
    result = evaluate(PerformanceInput(source="Bybit", spot_volume=1_200_000, spot_new_traders=5), vc)
    assert result.input.external
    assert any("скриншоты" in n for n in result.notes)
    assert any("VIP < 6" in n for n in result.notes)
    assert any("ПРИМЕРЫ-ЗАГЛУШКИ" in n for n in result.notes)


# --- ROI ------------------------------------------------------------------------

def test_roi_matches_template_formulas(vc):
    perf = PerformanceInput(spot_volume=1_000_000, futures_volume=10_000_000, upfront=1000, ltv=50, ftt=10,
                            competitor_spot_rate=40, competitor_futures_rate=30, competitor_upfront=2000)
    roi = compute_roi(perf, vc, spot_rate=30, futures_rate=20)
    # как в шаблоне: комиссия = объём × ставка комиссии, выплата = комиссия × тир + фикс
    assert roi.revenue == pytest.approx(330 + 135 + 2600 + 525)
    assert roi.rebates == pytest.approx(465 * 0.30 + 3125 * 0.20)
    assert roi.cost == pytest.approx(764.5 + 1000)
    assert roi.roi == pytest.approx(3590 / 1764.5 - 1)
    assert roi.roi_ltv == pytest.approx((50 * 10 + 3590) / 1764.5 - 1)
    assert roi.competitor_cost == pytest.approx(810 * 0.40 + 4125 * 0.30 + 2000)
    assert roi.vs_competitor == pytest.approx(1764.5 - 3561.5)


def test_roi_without_payout(vc):
    roi = compute_roi(PerformanceInput(spot_volume=1_000_000), vc, spot_rate=0, futures_rate=0)
    assert roi.roi is None and not roi.competitor


def test_proposed_rate_overrides(vc):
    result = evaluate(PerformanceInput(spot_volume=1_200_000, spot_new_traders=5, proposed_spot_rate=20), vc)
    assert result.markets["spot"].rate_used == 20


# --- ввод -------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, value",
    [("1.2M", 1.2e6), ("200k", 2e5), ("$3,000,000", 3e6), ("3 000 000", 3e6), ("2,5 млн", 2.5e6),
     ("1,5M", 1.5e6), ("93000000", 9.3e7), ("", 0.0), (150, 150.0)],
)
def test_parse_money(raw, value):
    assert parse_money(raw) == pytest.approx(value)


def test_parse_money_rejects_garbage():
    with pytest.raises(ValueError, match="не понял сумму"):
        parse_money("много")


def test_performance_from_mapping():
    perf = performance_from_mapping({"source": "OKX", "futures_volume": "30M", "futures_traders": "4",
                                     "top_ftt": "да", "comp_futures": "50%", "spot_rate": ""})
    assert perf.source_label == "OKX" and perf.futures_volume == 3e7 and perf.top_ftt_region
    assert perf.competitor_futures_rate == 50 and perf.proposed_spot_rate is None
    assert performance_from_mapping({"source": "OKX"}) is None   # без объёмов — нет блока


def test_batch_line_with_volumes():
    request = parse_batch_line('t.me/abcde source=Bybit spot_volume=1.2M futures_volume=30M ftt=8 top_ftt=1')
    assert request.links == ["t.me/abcde"]
    assert request.performance.spot_volume == 1.2e6
    assert request.performance.top_ftt_region
    with pytest.raises(LinkError, match="объёмы"):
        parse_batch_line("t.me/abcde spot_volume=много")


# --- карточка и рекомендация --------------------------------------------------------

def _qualifier(tmp_path, data=None, responses=None):
    settings = make_settings(tmp_path, make_config_dir(tmp_path))
    store = Store(settings.db_path)
    llm = None
    if responses is not None:
        settings.anthropic_api_key = "sk-test"
        llm = LLMClient(settings, client=FakeAnthropic(responses), cache=store)
    collectors = {"telegram": StaticCollector("telegram", data or {"good_channel": tg_data(followers=13556)})}
    return Qualifier(settings, make_config(settings), store=store, collectors=collectors, llm=llm, now=QNOW)


def test_card_with_volumes(tmp_path):
    # по соцсетям 28%, по объёмам 35% — основание заявки объёмы
    q = _qualifier(tmp_path, data={"good_channel": tg_data(followers=7000)})
    perf = PerformanceInput(source="Bybit", spot_volume=1_100_000, spot_new_traders=3,
                            futures_volume=7_000_000, top_ftt_region=True)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=perf))
    assert card.deal.basis == "volume"
    assert (card.deal.spot_rate, card.deal.futures_rate, card.deal.months) == (35, 35, 3)
    assert card.recommendation[0].startswith("По объёмам (критерий 1): Spot 35% на 3 мес")
    assert "Bybit" in card.recommendation[0]
    md = render_markdown(card)
    assert "ОБЪЁМЫ И ROI" in md and "Источник объёмов: Bybit" in md
    assert "Commission request: Spot 35%, 3 months (whitelisting)" in card.draft
    assert "Trading volume (Bybit, per month)" in card.draft
    assert "Social-media tier: 28%" in card.draft
    assert any("Соцсети (критерий 2): 28% — дополнительное основание" in l for l in card.recommendation)
    summary = card_summary(card)
    assert [t["value"] for t in summary["volumes"]["tiles"][:2]] == ["35%", "35%"]


def test_volumes_without_social_tier(tmp_path):
    q = _qualifier(tmp_path, data={"small_chan": tg_data(handle="small_chan", followers=1000, members=None, linked=False)})
    perf = PerformanceInput(spot_volume=1_200_000, spot_new_traders=5)
    card = q.run(PartnerRequest(links=["t.me/small_chan"], performance=perf))
    assert card.deal.submit
    assert any("по соцсетям партнёр ниже минимального тира" in line.lower() for line in card.recommendation)


def test_reject_when_neither_criterion(tmp_path):
    q = _qualifier(tmp_path, data={"small_chan": tg_data(handle="small_chan", followers=1000, members=None, linked=False)})
    card = q.run(PartnerRequest(links=["t.me/small_chan"], performance=PerformanceInput(spot_volume=10_000)))
    assert not card.deal.submit
    assert any("заявка будет отклонена" in line for line in card.recommendation)


def test_social_basis_when_volumes_short(tmp_path):
    q = _qualifier(tmp_path)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=PerformanceInput(spot_volume=10_000)))
    assert card.deal.basis == "social"
    assert any("Основание заявки — соцсети (критерий 2): 38%" in line for line in card.recommendation)


def test_negative_roi_warning(tmp_path):
    q = _qualifier(tmp_path)
    perf = PerformanceInput(spot_volume=1_200_000, spot_new_traders=5, upfront=50_000)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=perf))
    assert any("ROI отрицательный" in line for line in card.recommendation)
    assert card_summary(card)["volumes"]["tiles"][-1]["status"] == "critical"


def test_no_volume_section_without_input(tmp_path):
    card = _qualifier(tmp_path).run(PartnerRequest(links=["t.me/good_channel"]))
    assert card.performance is None
    assert "ОБЪЁМЫ И ROI" not in render_markdown(card)
    assert "Trading volume" not in card.draft     # пустой раздел черновика убран


def test_volumes_never_reach_llm(tmp_path):
    slots = {"AUDIENCE_SUMMARY": "a", "JUSTIFICATION": "b", "RISKS": "c"}
    q = _qualifier(tmp_path, responses=[assessment(), slots])
    perf = PerformanceInput(source="Bybit", futures_volume=93_123_456, upfront=47_321, ltv=777,
                            competitor_futures_rate=63)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=perf))
    sent = json.dumps(q.llm._client.messages.requests, ensure_ascii=False)
    for secret in ("93123456", "93,123,456", "93 123 456", "47321", "47,321", "Bybit", "777"):
        assert secret not in sent, f"{secret} ушло в LLM"
    assert "$93,123,456" in card.draft      # а в черновике — есть, подставлено локально


def test_missing_volume_config_warns(tmp_path):
    settings = make_settings(tmp_path, make_config_dir(tmp_path, volume=False))
    collectors = {"telegram": StaticCollector("telegram", {"good_channel": tg_data()})}
    config = make_config(settings)
    config.volume = None
    q = Qualifier(settings, config, store=Store(":memory:"), collectors=collectors, now=QNOW)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=PerformanceInput(spot_volume=1e6)))
    assert card.performance is None
    assert any("volume_criteria.yaml" in w for w in card.warnings)


# --- составное условие критерия 2: подписчики + средние просмотры ---------------------

def test_followers_with_views_condition(tmp_path):
    from qualifier.config import load_cpa, load_criteria
    from qualifier.models import GeoEstimate
    from qualifier.qualify import qualify
    from qualifier.stats import compute_metrics
    from qualifier.config import load_thresholds
    from qfixtures import items
    from qualifier.models import platform_key

    criteria_yaml = """
affiliate_types:
  individual:
    tiers:
      - rate: 60
        followers_with_views: {followers: 5000, avg_views: 2000}
      - rate: 20
        followers_single_platform: 1000
"""
    config_dir = make_config_dir(tmp_path, criteria_yaml)
    criteria, cpa, th = load_criteria(config_dir), load_cpa(config_dir), load_thresholds(config_dir)

    def run(views):
        data = tg_data(followers=6000, posts=items(20, views=views))
        metrics = {platform_key(data): compute_metrics(data, th, QNOW)}
        return qualify([data], criteria, cpa, GeoEstimate(), metrics=metrics)

    high = run(2500)
    assert high.tier_rate == 60 and high.tier_met_by == ["followers_with_views"]
    low = run(1500)
    assert low.tier_rate == 20
    gap = next(g for g in low.next_tier_gaps if g.metric == "followers_with_views")
    assert (gap.have, gap.need, gap.missing) == (1500, 2000, 500)
    assert "5 000+" in gap.detail


# --- веб ---------------------------------------------------------------------------------

def test_web_accepts_volumes(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from qualifier.web import WebState, create_app

    # как в сервере: Qualifier (и его SQLite) создаётся в рабочем потоке
    client = TestClient(create_app(WebState(lambda: _qualifier(tmp_path), token="t")))
    payload = {"links": "t.me/good_channel",
               "performance": {"source": "Bybit", "spot_volume": "1.1M", "spot_traders": "3"}}
    response = client.post("/api/qualify?token=t", json=payload)
    assert response.status_code == 200
    volumes = response.json()["summary"]["volumes"]
    assert volumes["source"] == "Bybit" and volumes["tiles"][0]["value"] == "35%"
    bad = client.post("/api/qualify?token=t", json={"links": "t.me/good_channel",
                                                    "performance": {"spot_volume": "много"}})
    assert bad.status_code == 400 and "объёмы" in bad.json()["error"]


def test_market_line_wording(vc):
    from qualifier.performance import market_line

    # автооценка 25%, whitelisting выше — «не нужен» писать нельзя
    m = evaluate(PerformanceInput(spot_volume=1_100_000, spot_new_traders=3), vc).markets["spot"]
    line = market_line(m, 3)
    assert "whitelisting даёт 35% на 3 мес" in line and "не нужен" not in line
    m = evaluate(PerformanceInput(spot_volume=1_200_000, spot_new_traders=5), vc).markets["spot"]
    assert "whitelisting не нужен" in market_line(m, 3)


def test_social_wins_when_higher_than_volumes(tmp_path):
    # соцсети дают 38%, объёмы — только 25% по автооценке: основание заявки — соцсети
    q = _qualifier(tmp_path)
    perf = PerformanceInput(spot_volume=150_000, spot_new_traders=2)
    card = q.run(PartnerRequest(links=["t.me/good_channel"], performance=perf))
    assert card.deal.basis == "social" and card.deal.spot_rate is None
    assert card.recommendation[0].startswith("Объёмы (критерий 1) дают меньше, чем соцсети: Spot 25%")
    assert any("Основание заявки — соцсети (критерий 2): 38%" in line for line in card.recommendation)
    assert not any("дополнительное основание" in line for line in card.recommendation)
    assert "Commission request: 38% (Criteria 2 — social media)" in card.draft
