"""Карточка (markdown/JSON/CSV), черновик, кэш, лимиты, история, CLI и веб."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from qfixtures import (
    QNOW,
    FakeAnthropic,
    StaticCollector,
    assessment,
    items,
    make_config,
    make_config_dir,
    make_settings,
    tg_data,
)
from qualifier import cli
from qualifier.draft import parse_template, render
from qualifier.llm import LLMClient
from qualifier.models import PartnerRequest, PlatformData
from qualifier.render import card_csv_row, render_json, render_markdown, sort_key
from qualifier.service import BudgetExceeded, Qualifier
from qualifier.store import Store

REPO = Path(__file__).resolve().parents[1]

AR_POSTS = [
    "🚀 إيردروب جديد! اعمل claim للنقاط قبل نهاية الأسبوع",
    "مكافأة 500 جنيه لأول 100 مشترك 🎁 التحويل على فودافون كاش",
    "#إعلان منصة جديدة https://ex.example/register?ref=MASR",
    "تحديث السوق: البيتكوين فوق 60 ألف والألتكوينز بتتحرك",
]


def _spec_partner() -> dict[str, PlatformData]:
    return {
        "crypto_masr": tg_data(
            handle="crypto_masr", followers=13556, title="Crypto Masr",
            description="قناة الإيردروب في مصر — اربح يوميًا. للإعلانات: @masr_ads",
            posts=items(30, views=1820, reactions=41, text=AR_POSTS, step_hours=22),
        )
    }


def _qualifier(tmp_path, data=None, responses=None, **overrides) -> Qualifier:
    settings = make_settings(tmp_path, make_config_dir(tmp_path), **overrides)
    store = Store(settings.db_path)
    llm = None
    if responses is not None:
        settings.anthropic_api_key = "sk-test"
        llm = LLMClient(settings, client=FakeAnthropic(responses), cache=store)
    collectors = {
        "telegram": StaticCollector("telegram", data or _spec_partner()),
        "youtube": StaticCollector("youtube", {}),
        "x": StaticCollector("x", {}),
    }
    return Qualifier(settings, make_config(settings), store=store, collectors=collectors, llm=llm, now=QNOW)


AIRDROP = assessment(
    audience_type={"value": "airdrop_hunters", "secondary": "traders",
                   "evidence": ["«إيردروب جديد! اعمل claim للنقاط» [TG#1]"]},
    conversion_forecast={"value": "low", "reason": "преобладает контент про раздачи и клейм-поинты",
                         "evidence": ["большинство постов — айрдропы"]},
    airdrop_share={"value": "dominant", "evidence": ["3 из 4 постов — айрдропы и бонусы"]},
    red_flags={"status": "found", "items": [
        {"type": "get_rich_quick", "quote": "اربح يوميًا", "comment": "в описании канала обещание заработка"}]},
)


def test_card_matches_spec_structure(tmp_path):
    q = _qualifier(tmp_path, responses=[AIRDROP, {"AUDIENCE_SUMMARY": "Airdrop audience.",
                                                  "JUSTIFICATION": "- Big reach", "RISKS": "- Airdrops"}])
    card = q.run(PartnerRequest(links=["t.me/crypto_masr"], notes="asks for 50%"))
    md = render_markdown(card, (REPO / "templates" / "card.md").read_text(encoding="utf-8"))

    for heading in ("ПАРТНЁР:", "МЕТРИКИ", "АУДИТОРИЯ", "КВАЛИФИКАЦИЯ", "ФЛАГИ", "РЕКОМЕНДАЦИЯ", "ЧЕРНОВИК ЗАЯВКИ"):
        assert heading in md
    assert "Площадки: Telegram 13 556 подписчиков" in md
    assert "Подписчики (макс. площадка): 13 556" in md
    assert "Медиана просмотров: 1 820 (ER 13.4%)" in md
    assert "Комьюнити-чат: есть" in md
    assert "Продаёт рекламу: да" in md
    assert "Контакт: @masr_ads" in md
    assert "Гео: Египет (уверенность: medium — подтвердить у партнёра)" in md
    assert "Язык: арабский" in md
    assert "Тип аудитории: айрдроп-охотники с частью трейдеров" in md
    assert "Прогноз конверсии в объём: низкий" in md
    assert "Обоснование: преобладает контент про раздачи и клейм-поинты" in md
    assert "Предполагаемый тир: 38%" in md
    assert "До следующего тира (48%): не хватает 2 444 подписчиков на одной площадке" in md
    assert "CPA по гео (EG): $12 за FTT" in md
    assert "[!] airdrop_heavy — аудитория даёт регистрации, но слабо конвертируется в объём" in md
    assert "[!] brand_risk" in md
    assert "Старт на 38% на тестовый период" in md
    assert "Квалификация предварительная, основана на публичных данных" in md
    assert "asks for 50%" in card.draft
    assert "**Subject:** Rate increase request" in card.draft


def test_json_and_csv_outputs(tmp_path):
    q = _qualifier(tmp_path)
    card = q.run(PartnerRequest(links=["t.me/crypto_masr"]))
    data = json.loads(render_json(card))
    assert data["qualification"]["tier_rate"] == 38
    assert data["platforms"][0]["metrics"]["er"] == 13.4
    assert data["geo"]["needs_confirmation"] is True
    assert "items" not in data["platforms"][0]
    row = card_csv_row(card)
    assert row["tier"] == "38" and row["er"] == 13.4 and row["geo"] == "EG"


def test_draft_template_parsing():
    body, slots = parse_template(
        "<!-- комментарий с {{X}} и [[Y: z]] -->\n"
        "Hi {{PARTNER_NAME}}\n[[WHY: explain why]]\n[[WHY]]\n{{UNKNOWN}}"
    )
    assert slots == [("WHY", "explain why")]
    text = render(body, {"PARTNER_NAME": "Masr"}, {"WHY": "because"})
    assert text == "Hi Masr\nbecause\nbecause\n{{UNKNOWN}}\n"


def test_russian_draft(tmp_path):
    q = _qualifier(tmp_path, draft_language="ru")
    card = q.run(PartnerRequest(links=["t.me/crypto_masr"]))
    assert "**Тема:** Заявка на повышение ставки" in card.draft
    assert "Запрашиваемая ставка: 38%" in card.draft
    assert "до тира 48%" in card.draft.lower()


def test_platform_cache_and_refresh(tmp_path):
    q = _qualifier(tmp_path)
    collector = q.collectors["telegram"]
    q.run(PartnerRequest(links=["t.me/crypto_masr"]))
    card = q.run(PartnerRequest(links=["t.me/crypto_masr"]))
    assert collector.calls == ["crypto_masr"]
    assert card.platforms[0].from_cache
    assert any("из кэша" in w for w in card.warnings)
    q.run(PartnerRequest(links=["t.me/crypto_masr"], refresh=True))
    assert collector.calls == ["crypto_masr", "crypto_masr"]


def test_cache_expires(tmp_path):
    from datetime import timedelta

    clock = {"now": QNOW}
    store = Store(tmp_path / "c.db", ttl_days=7, clock=lambda: clock["now"])
    store.cache_set("platform", "k", {"a": 1})
    assert store.cache_get("platform", "k") == {"a": 1}
    clock["now"] = QNOW + timedelta(days=8)
    assert store.cache_get("platform", "k") is None
    assert store.cache_purge() == 1


def test_channel_budget(tmp_path):
    data = {f"chan_{i}": tg_data(handle=f"chan_{i}") for i in range(3)}
    q = _qualifier(tmp_path, data=data, max_channels_per_run=2)
    q.run(PartnerRequest(links=["t.me/chan_0", "t.me/chan_1"]))
    with pytest.raises(BudgetExceeded):
        q.run(PartnerRequest(links=["t.me/chan_2"]))
    # из кэша — без расхода лимита
    q.run(PartnerRequest(links=["t.me/chan_0"]))


def test_unavailable_platform_needs_manual_review(tmp_path):
    q = _qualifier(tmp_path)
    card = q.run(PartnerRequest(links=["t.me/crypto_masr", "x.com/crypto_masr"]))
    assert card.primary.platform == "telegram"
    assert card.qualification.manual_review
    assert any("X @crypto_masr" in r for r in card.qualification.manual_review_reasons)
    md = render_markdown(card)
    assert "X — данные недоступны" in md


def test_history_saved(tmp_path):
    q = _qualifier(tmp_path)
    card = q.run(PartnerRequest(links=["t.me/crypto_masr"]))
    path = q.save(card)
    assert path is not None and path.exists()
    assert "ПАРТНЁР: Crypto Masr" in path.read_text(encoding="utf-8")
    rows = q.store.history(30)
    assert rows[0]["tier"] == 38 and rows[0]["geo"] == "EG" and rows[0]["report_path"] == str(path)


def test_batch_sorting():
    rows = [{"tier": "30", "er": 20}, {"tier": "", "er": 50}, {"tier": "40", "er": 5}, {"tier": "40", "er": 12}]
    ordered = sorted(rows, key=sort_key)
    assert [(r["tier"], r["er"]) for r in ordered] == [("40", 12), ("40", 5), ("30", 20), ("", 50)]


# --- CLI в демо-режиме ---------------------------------------------------------

@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    return tmp_path


def test_cli_default_command_is_check():
    assert cli._normalize_argv(["t.me/x", "--json"]) == ["check", "t.me/x", "--json"]
    assert cli._normalize_argv(["--demo", "--db", "a.db", "t.me/x"]) == ["--demo", "--db", "a.db", "check", "t.me/x"]
    assert cli._normalize_argv(["batch", "f.txt"]) == ["batch", "f.txt"]
    assert cli._normalize_argv(["history", "--days", "7"]) == ["history", "--days", "7"]


def test_cli_demo_check(demo_env, capsys):
    db = str(demo_env / "q.db")
    code = cli.main(["--demo", "--db", db, "t.me/demo_airdrop_eg", "youtube.com/@demo_airdrop_eg",
                     "--notes", "met at conference"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ПАРТНЁР: Crypto Masr" in out
    assert "Площадки: Telegram 13 556 подписчиков | YouTube 4 200 подписчиков" in out
    assert "Карточка сохранена" in out
    assert list((demo_env / "reports").glob("*.md"))


def test_cli_demo_json(demo_env, capsys):
    code = cli.main(["--demo", "--db", str(demo_env / "q.db"), "t.me/demo_futures_ru", "--json", "--no-save"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["audience"]["audience_type"]["value"] == "traders"
    assert data["geo"]["country"] == "RU"


def test_cli_bad_link(demo_env, capsys):
    assert cli.main(["--demo", "--db", str(demo_env / "q.db"), "t.me/+secret"]) == 2
    assert "приватная" in capsys.readouterr().err


def test_cli_batch_and_history(demo_env, capsys):
    links = demo_env / "links.txt"
    links.write_text(
        "# партнёры\n"
        "t.me/demo_futures_ru geo=RU\n"
        "t.me/demo_airdrop_eg youtube.com/@demo_airdrop_eg\n"
        "t.me/+private\n"
        "t.me/demo_pump_signals\n",
        encoding="utf-8",
    )
    out_csv = demo_env / "out.csv"
    db = str(demo_env / "q.db")
    assert cli.main(["--demo", "--db", db, "batch", str(links), "--csv", str(out_csv)]) == 0
    with out_csv.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4
    assert rows[-1]["status"].startswith("ссылка")              # пропущенные — в конце
    tiers = [float(r["tier"]) for r in rows if r["tier"]]
    assert tiers == sorted(tiers, reverse=True)
    capsys.readouterr()
    assert cli.main(["--db", db, "history", "--days", "30"]) == 0
    assert "Проверки за 30 дней: 3" in capsys.readouterr().out


def test_cli_single_csv_appends(demo_env):
    out_csv = demo_env / "longlist.csv"
    db = str(demo_env / "q.db")
    cli.main(["--demo", "--db", db, "t.me/demo_futures_ru", "--csv", str(out_csv), "--no-save"])
    cli.main(["--demo", "--db", db, "t.me/demo_pump_signals", "--csv", str(out_csv), "--no-save"])
    with out_csv.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["handle"] for r in rows] == ["@demo_futures_ru", "@demo_pump_signals"]


def test_cli_config_check_offline(demo_env, capsys):
    code = cli.main(["--env", str(demo_env / "missing.env"), "config", "check", "--offline"])
    out = capsys.readouterr().out
    assert "criteria.yaml" in out and "geo_markers.yaml" in out
    assert "[✓] .gitignore" in out
    assert code == 0  # предупреждения — не ошибки


# --- веб -------------------------------------------------------------------------

def test_web_requires_token_and_qualifies(demo_env):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from qualifier.web import WebState, create_app

    args = cli.build_parser().parse_args(["--demo", "--db", ":memory:", "history"])
    state = WebState(lambda: cli.build_qualifier(args, cli._settings(args)), token="secret")
    client = TestClient(create_app(state))
    assert client.get("/").status_code == 403
    assert client.post("/api/qualify", json={"links": "t.me/demo_futures_ru"}).status_code == 403
    page = client.get("/?token=secret")
    assert page.status_code == 200 and "Квалификатор партнёра" in page.text
    response = client.post("/api/qualify", json={"links": "t.me/demo_futures_ru", "type": "individual"})
    assert response.status_code == 200
    assert "ПАРТНЁР: Фьючерсы без иллюзий" in response.json()["markdown"]
    bad = client.post("/api/qualify", json={"links": "t.me/demo_futures_ru", "geo": "Russia"})
    assert bad.status_code == 400
    history = client.get("/api/history").json()
    assert history and history[0]["partner"].startswith("Фьючерсы")


def test_card_summary_tiles_and_verdict(tmp_path):
    from qualifier.render import card_summary

    q = _qualifier(tmp_path, responses=[AIRDROP, {"AUDIENCE_SUMMARY": "a", "JUSTIFICATION": "b", "RISKS": "c"}])
    summary = card_summary(q.run(PartnerRequest(links=["t.me/crypto_masr"])))
    tiles = {t["label"]: t for t in summary["tiles"]}
    assert tiles["Предполагаемый тир"]["value"] == "38%"
    assert tiles["Предполагаемый тир"]["note"] == "до 48%: −2 444 подписчиков"
    assert tiles["Подписчики (макс.)"]["value"] == "13 556"
    assert tiles["ER по медиане"]["value"] == "13.4%"
    assert tiles["Гео"]["status"] == "warn"           # medium — подтвердить у партнёра
    assert tiles["CPA по гео"]["value"] == "$12"
    assert summary["verdict"] == "warn"
    assert {f["code"] for f in summary["flags"]} == {"airdrop_heavy", "brand_risk"}


def test_web_page_uses_binance_palette_and_safe_dom():
    from qualifier.web import PAGE

    assert "#FCD535" in PAGE and "#0B0E11" in PAGE      # жёлтый акцент и тёмный фон
    assert 'data-theme="light"' in PAGE                 # светлая тема переключателем
    # данные каналов вставляются только через textContent
    assert "innerHTML" not in PAGE
