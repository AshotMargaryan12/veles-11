"""Демо-режим: встроенный корпус вместо Telegram."""

from __future__ import annotations

import pytest

from tghunter.demo import DemoGateway, corpus_size
from tghunter.metrics import collect_metrics
from tghunter.scoring import score_channel


@pytest.fixture
def gateway():
    return DemoGateway()


def test_corpus_is_not_empty():
    assert corpus_size() >= 20


def test_every_channel_resolves(gateway):
    for username in gateway.snapshots:
        assert gateway.fetch_snapshot(username) is not None


def test_unknown_channel_returns_none(gateway):
    assert gateway.fetch_snapshot("no_such_channel_here") is None


def test_keyword_search_matches_by_tag(gateway):
    found = {c.username for c in gateway.search_keyword("фьючерсы")}
    assert "demo_futures_desk" in found
    assert "demo_scalping_lab" in found
    assert "demo_money_basics" not in found  # другая тематика


def test_keyword_search_is_case_insensitive(gateway):
    upper = [c.username for c in gateway.search_keyword("ТРЕЙДИНГ")]
    lower = [c.username for c in gateway.search_keyword("трейдинг")]
    assert upper == lower and upper


def test_empty_keyword_finds_nothing(gateway):
    assert gateway.search_keyword("   ") == []


def test_keyword_search_respects_limit(gateway):
    assert len(gateway.search_keyword("крипта", limit=2)) == 2


def test_all_candidates_carry_keywords_method(gateway):
    for c in gateway.search_keyword("инвестиции"):
        assert c.method == "keywords"
        assert c.channel_id


def test_similar_returns_known_channels(gateway):
    found = gateway.similar_channels("demo_futures_desk")
    assert {c.username for c in found} <= set(gateway.snapshots)
    assert all(c.method == "similar" for c in found)
    assert all(c.source_channel == "@demo_futures_desk" for c in found)


def test_similar_of_unknown_channel_is_empty(gateway):
    assert gateway.similar_channels("nobody_here") == []


def test_mentions_graph(gateway):
    found = gateway.channel_mentions("demo_futures_desk")
    assert {c.username for c in found} == {"demo_quiet_analyst", "demo_defi_digest"}


def test_monetized_channel_has_ad_markers(gateway):
    snapshot = gateway.fetch_snapshot("demo_futures_desk")
    metrics = collect_metrics(snapshot, blacklist_markers=[], stream="demo")
    assert metrics["monetized"] is True
    assert metrics["contact"] == "@demo_adv_one"


def test_clean_channel_is_not_monetized(gateway):
    snapshot = gateway.fetch_snapshot("demo_family_budget")
    metrics = collect_metrics(snapshot, blacklist_markers=[], stream="demo")
    assert metrics["monetized"] is False


def test_pumper_trips_the_blacklist(gateway):
    from tghunter.config import load_blacklist

    snapshot = gateway.fetch_snapshot("demo_pump_squad")
    metrics = collect_metrics(snapshot, blacklist_markers=load_blacklist("config"), stream="demo")
    assert metrics["blacklist_hit"] is True
    assert "памп" in metrics["blacklist_markers"]


def test_dead_channel_reads_as_dead(gateway):
    snapshot = gateway.fetch_snapshot("demo_ghost_trader")
    metrics = collect_metrics(snapshot, blacklist_markers=[], stream="demo")
    assert metrics["days_since_last_post"] >= 60
    assert metrics["posts_last_30d"] == 0


@pytest.mark.parametrize(
    "username, expected",
    [("demo_futures_desk", "ru"), ("demo_london_desk", "en"), ("demo_kz_finance", "turkic")],
)
def test_corpus_languages_are_detectable(gateway, username, expected):
    snapshot = gateway.fetch_snapshot(username)
    assert collect_metrics(snapshot, blacklist_markers=[], stream="demo")["language"] == expected


def test_corpus_spans_the_whole_score_range(gateway):
    scores = []
    for username in gateway.snapshots:
        snapshot = gateway.fetch_snapshot(username, method="keywords")
        scores.append(score_channel(collect_metrics(snapshot, [], "demo"))[0])
    assert min(scores) <= 10
    assert max(scores) >= 70


def test_demo_search_end_to_end(tmp_path, capsys):
    """`--demo search` работает без сети, без сессии и без Telethon."""
    from tghunter.cli import main

    code = main(["--demo", "--db", str(tmp_path / "d.db"),
                 "search", "трейдинг", "--no-export"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Запрос: трейдинг" in out
    assert "@demo_futures_desk" in out


def test_demo_expand_awards_similar_bonus(tmp_path, capsys):
    from tghunter.cli import main
    from tghunter.db import Database

    db_path = tmp_path / "d.db"
    main(["--demo", "--db", str(db_path), "search", "фьючерсы", "--expand", "--no-export"])

    with Database(db_path) as db:
        row = db.find(username="demo_btc_watch")  # приходит только через similar
        assert row is not None
        assert row["method"] == "similar"
        assert "similar+25" in row["score_breakdown"]


def test_demo_second_search_creates_no_duplicates(tmp_path, capsys):
    from tghunter.cli import main
    from tghunter.db import Database

    db_path = tmp_path / "d.db"
    main(["--demo", "--db", str(db_path), "search", "трейдинг", "--no-export"])
    capsys.readouterr()
    main(["--demo", "--db", str(db_path), "search", "трейдинг", "--no-export"])

    out = capsys.readouterr().out
    assert "новых 0" in out
    assert "уже в базе" in out
    with Database(db_path) as db:
        usernames = [r["username"] for r in db.rows_by_ids(
            [r["channel_id"] for r in db.export_rows()])]
        assert len(usernames) == len(set(usernames))


def test_demo_run_stream_works(tmp_path, capsys):
    """Демо-режим работает и для прогона пресета, не только для поиска."""
    from tghunter.cli import main

    code = main(["--demo", "--db", str(tmp_path / "d.db"),
                 "run", "--stream", "crypto_core", "--no-export"])
    assert code == 0
    assert "[crypto_core]" in capsys.readouterr().out


def test_demo_export_writes_csv(tmp_path, monkeypatch, capsys):
    from tghunter.cli import main

    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    main(["--demo", "--db", str(tmp_path / "d.db"), "search", "инвестиции"])

    files = list((tmp_path / "exports").rglob("*.csv"))
    assert files
    content = files[0].read_text(encoding="utf-8-sig")
    assert "score" in content and "demo_" in content
