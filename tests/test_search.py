"""Свободный поиск по запросу: `tghunter search "..."`."""

from __future__ import annotations

import pytest

from conftest import FakeGateway, make_posts, make_snapshot
from tghunter.models import Candidate
from tghunter.search import build_adhoc_stream, format_table, run_search, split_queries


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("трейдинг", ["трейдинг"]),
        ("трейдинг, скальпинг", ["трейдинг", "скальпинг"]),
        ("  трейдинг ,  , скальпинг  ", ["трейдинг", "скальпинг"]),
        ("", []),
    ],
)
def test_split_queries(raw, expected):
    assert split_queries(raw) == expected


def test_adhoc_stream_defaults_to_any_language():
    stream = build_adhoc_stream(["трейдинг"])
    assert stream.languages == []      # пустой список = язык не фильтруется
    assert stream.keywords == ["трейдинг"]
    assert stream.methods == ["keywords"]
    assert stream.longlist_mode is False


def test_loose_flag_enables_longlist_mode():
    assert build_adhoc_stream(["x"], loose=True).longlist_mode is True


def test_any_language_passes_english_channel():
    """Без --lang англоязычный канал не должен отсекаться."""
    from tghunter.filters import check_filters

    metrics = {"subscribers": 9000, "er": 8.0, "comments_enabled": True,
               "avg_reactions": 10, "days_since_last_post": 1, "posts_last_30d": 10,
               "language": "en", "blacklist_hit": False}
    assert check_filters(metrics, build_adhoc_stream(["crypto"]))[0]


def gateway_with(*names, **kwargs):
    snapshots = {}
    for name in names:
        snapshots[name] = make_snapshot(
            username=name,
            channel_id=abs(hash(name)) % 10000,
            subscribers=20000,
            about="Крипта и трейдинг. По рекламе: @adv_manager",
            posts=make_posts(count=15, views=2000, reactions=30,
                             text="Обзор рынка криптовалют и деньги для инвесторов"),
        )
    return FakeGateway(snapshots=snapshots, **kwargs)


def test_search_returns_ranked_rows(db, settings, limiter):
    gateway = gateway_with(
        "found_one", "found_two",
        keyword_hits={"трейдинг": [Candidate(username="found_one", method="keywords"),
                                   Candidate(username="found_two", method="keywords")]},
    )
    stream = build_adhoc_stream(["трейдинг"])
    result = run_search(gateway, db, settings, limiter, ["трейдинг"], stream,
                        config_dir="config")

    assert result["found"] == 2
    assert result["new"] == 2
    assert result["status"] == "ok"
    scores = [r["score"] for r in result["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert all(r["known"] is False for r in result["rows"])


def test_search_marks_channels_already_in_base(db, settings, limiter):
    """Известный канал показывается из базы и не стоит запроса к API."""
    db.import_existing(["@found_one"])
    gateway = gateway_with(
        "found_one", "found_two",
        keyword_hits={"трейдинг": [Candidate(username="found_one", method="keywords"),
                                   Candidate(username="found_two", method="keywords")]},
    )
    stream = build_adhoc_stream(["трейдинг"])
    result = run_search(gateway, db, settings, limiter, ["трейдинг"], stream,
                        config_dir="config")

    assert result["known"] == 1
    assert gateway.fetch_calls == ["found_two"]
    known_row = next(r for r in result["rows"] if r["username"] == "found_one")
    assert known_row["known"] is True


def test_search_expand_pulls_similar(db, settings, limiter):
    gateway = gateway_with(
        "seed_hit", "lookalike",
        keyword_hits={"трейдинг": [Candidate(username="seed_hit", method="keywords")]},
        similar_hits={"seed_hit": [Candidate(username="lookalike", method="similar",
                                             source_channel="@seed_hit")]},
    )
    stream = build_adhoc_stream(["трейдинг"])
    result = run_search(gateway, db, settings, limiter, ["трейдинг"], stream,
                        expand=True, config_dir="config")

    usernames = {r["username"] for r in result["rows"]}
    assert usernames == {"seed_hit", "lookalike"}
    assert db.find(username="lookalike")["method"] == "similar"


def test_search_without_expand_skips_similar(db, settings, limiter):
    gateway = gateway_with(
        "seed_hit",
        keyword_hits={"трейдинг": [Candidate(username="seed_hit", method="keywords")]},
        similar_hits={"seed_hit": [Candidate(username="never", method="similar")]},
    )
    stream = build_adhoc_stream(["трейдинг"])
    run_search(gateway, db, settings, limiter, ["трейдинг"], stream, config_dir="config")
    assert not any(call.startswith("similar:") for call in gateway.search_calls)


def test_search_respects_channel_budget(db, settings, limiter):
    names = [f"chan_{i}" for i in range(5)]
    gateway = gateway_with(
        *names,
        keyword_hits={"крипта": [Candidate(username=n, method="keywords") for n in names]},
    )
    stream = build_adhoc_stream(["крипта"])
    result = run_search(gateway, db, settings, limiter, ["крипта"], stream,
                        max_channels=2, config_dir="config")

    assert result["status"] == "budget_exhausted"
    assert len(gateway.fetch_calls) == 2


def test_search_aborts_on_floodwait_streak(db, settings, limiter):
    from tghunter.ratelimit import RunAborted

    class Flooding(FakeGateway):
        def search_keyword(self, keyword, limit=30):
            raise RunAborted("2 FloodWait подряд — остановлен по лимитам")

    stream = build_adhoc_stream(["крипта"])
    result = run_search(Flooding(), db, settings, limiter, ["крипта"], stream,
                        config_dir="config")
    assert result["aborted"] is True
    assert result["rows"] == []


def test_multiple_queries_are_all_searched(db, settings, limiter):
    gateway = gateway_with(
        "a_chan", "b_chan",
        keyword_hits={"трейдинг": [Candidate(username="a_chan", method="keywords")],
                      "скальпинг": [Candidate(username="b_chan", method="keywords")]},
    )
    stream = build_adhoc_stream(["трейдинг", "скальпинг"])
    result = run_search(gateway, db, settings, limiter, ["трейдинг", "скальпинг"],
                        stream, config_dir="config")

    assert result["found"] == 2
    assert gateway.search_calls == ["keyword:трейдинг", "keyword:скальпинг"]


def test_format_table_renders_verdicts():
    rows = [
        {"score": 90, "username": "good", "channel_id": 1, "subscribers": 20000, "er": 9.1,
         "comments_enabled": 1, "contact": "@adv", "passed_filters": 1, "blacklist_hit": 0,
         "blacklist_markers": "", "filter_reasons": "", "known": False},
        {"score": 40, "username": "seen", "channel_id": 2, "subscribers": 8000, "er": 5.0,
         "comments_enabled": 0, "contact": None, "passed_filters": 1, "blacklist_hit": 0,
         "blacklist_markers": "", "filter_reasons": "", "known": True},
        {"score": 0, "username": "pump", "channel_id": 3, "subscribers": 9000, "er": 12.0,
         "comments_enabled": 1, "contact": "@x", "passed_filters": 0, "blacklist_hit": 1,
         "blacklist_markers": "памп", "filter_reasons": "", "known": False},
    ]
    table = format_table(rows)
    assert "@good" in table and "в выгрузку" in table
    assert "уже в базе" in table
    assert "blacklist: памп" in table


def test_format_table_handles_empty():
    assert format_table([]) == "Ничего не найдено."


def test_search_command_end_to_end(tmp_path, monkeypatch, capsys):
    import tghunter.cli as cli

    gateway = gateway_with(
        "cli_found",
        keyword_hits={"трейдинг": [Candidate(username="cli_found", method="keywords")]},
    )
    monkeypatch.setattr(cli, "_gateway", lambda *a, **kw: gateway)
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("RATE_MIN_INTERVAL", "0")
    monkeypatch.setenv("RATE_MAX_INTERVAL", "0")

    code = cli.main(["--db", str(tmp_path / "s.db"), "search", "трейдинг"])
    assert code == 0

    out = capsys.readouterr().out
    assert "Запрос: трейдинг" in out
    assert "@cli_found" in out
    assert "CSV:" in out
    assert list((tmp_path / "exports").rglob("search_*.csv"))


def test_search_command_prompts_when_no_query(tmp_path, monkeypatch, capsys):
    import tghunter.cli as cli

    gateway = gateway_with(
        "typed_hit",
        keyword_hits={"крипта": [Candidate(username="typed_hit", method="keywords")]},
    )
    monkeypatch.setattr(cli, "_gateway", lambda *a, **kw: gateway)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "крипта")
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))

    assert cli.main(["--db", str(tmp_path / "s.db"), "search"]) == 0
    assert "@typed_hit" in capsys.readouterr().out


def test_empty_query_is_rejected(tmp_path, monkeypatch, capsys):
    import tghunter.cli as cli

    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    code = cli.main(["--db", str(tmp_path / "s.db"), "search"])
    assert code == 1
    assert "Пустой запрос" in capsys.readouterr().err
