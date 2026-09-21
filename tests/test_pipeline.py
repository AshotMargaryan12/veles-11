"""Сквозной прогон конвейера (разделы 3-7, 9 ТЗ)."""

from __future__ import annotations

import pytest

from conftest import FakeGateway, make_posts, make_snapshot
from tghunter.models import Candidate
from tghunter.pipeline import (
    RUN_STATUS_BUDGET,
    RUN_STATUS_LIMITED,
    RUN_STATUS_OK,
    Pipeline,
)
from tghunter.ratelimit import RunAborted


@pytest.fixture
def crypto_stream(streams):
    return streams["crypto_core"]


def build_pipeline(gateway, db, settings, limiter):
    return Pipeline(gateway, db, settings, limiter, config_dir="config")


def test_full_run_finds_scores_and_stores_channel(db, settings, limiter, crypto_stream):
    good = make_snapshot(
        username="good_one",
        channel_id=501,
        subscribers=20000,
        about="Крипта и трейдинг. По рекламе: @adv_manager",
        posts=make_posts(count=15, views=2000, reactions=30,
                         text="Обзор рынка криптовалют и деньги #реклама erid: 2Vfn"),
    )
    gateway = FakeGateway(
        snapshots={"good_one": good},
        keyword_hits={"трейдинг": [Candidate(username="good_one", method="keywords")]},
    )
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]

    result = build_pipeline(gateway, db, settings, limiter).run_stream(stream)

    assert result["status"] == RUN_STATUS_OK
    assert (result["checked"], result["new"]) == (1, 1)

    row = db.find(username="good_one")
    assert row["stream"] == "crypto_core"
    assert row["er"] == pytest.approx(10.0)
    assert row["monetized"] == 1
    assert row["contact"] == "@adv_manager"
    assert row["passed_filters"] == 1
    assert row["status"] == "new"
    assert row["score"] >= 65  # монетизация + высокий ER + контакт + комменты + частота


def test_similar_channel_gets_similar_bonus(db, settings, limiter, crypto_stream):
    snapshot = make_snapshot(username="look_alike", channel_id=601, subscribers=20000)
    gateway = FakeGateway(
        snapshots={"look_alike": snapshot},
        similar_hits={"partner": [Candidate(username="look_alike", method="similar",
                                            source_channel="@partner")]},
    )
    stream = crypto_stream
    stream.methods = ["similar"]
    pipeline = build_pipeline(gateway, db, settings, limiter)
    pipeline._seeds = lambda _s: ["@partner"]

    pipeline.run_stream(stream)
    row = db.find(username="look_alike")
    assert row["method"] == "similar"
    assert row["source_channel"] == "@partner"
    assert "similar+25" in row["score_breakdown"]


def test_second_run_creates_no_duplicates(db, settings, limiter, crypto_stream):
    """Раздел 12, Э3: повторный прогон не создаёт дублей."""
    snapshot = make_snapshot(username="repeat_one", channel_id=701, subscribers=20000)
    gateway = FakeGateway(
        snapshots={"repeat_one": snapshot},
        keyword_hits={"трейдинг": [Candidate(username="repeat_one", method="keywords")]},
    )
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]
    pipeline = build_pipeline(gateway, db, settings, limiter)

    first = pipeline.run_stream(stream)
    second = pipeline.run_stream(stream)

    assert first["new"] == 1
    assert second["new"] == 0
    assert second["checked"] == 0  # известный канал не стоил ни одного запроса к API
    assert db.stats()["total"] == 1
    assert gateway.fetch_calls == ["repeat_one"]


def test_pumper_is_blacklisted_and_not_exported(db, settings, limiter, streams):
    """Раздел 12, Э3: памперы отсекаются."""
    pumper = make_snapshot(
        username="pump_king",
        channel_id=801,
        title="ПАМП сигналы x100",
        about="Бесплатные сигналы, иксы за день",
        subscribers=20000,
    )
    gateway = FakeGateway(
        snapshots={"pump_king": pumper},
        keyword_hits={"сигналы": [Candidate(username="pump_king", method="keywords")]},
    )
    stream = streams["signals"]
    stream.keywords = ["сигналы"]
    stream.methods = ["keywords"]

    build_pipeline(gateway, db, settings, limiter).run_stream(stream)

    row = db.find(username="pump_king")
    assert row["blacklist_hit"] == 1
    assert "памп" in row["blacklist_markers"]
    assert row["status"] == "blacklist"
    assert db.export_rows() == []          # из выгрузки исключён
    assert db.exists(username="pump_king")  # но остался в базе


def test_imported_crm_channel_is_not_rediscovered(db, settings, limiter, crypto_stream):
    db.import_existing(["@already_worked"])
    gateway = FakeGateway(
        snapshots={"already_worked": make_snapshot(username="already_worked")},
        keyword_hits={"трейдинг": [Candidate(username="already_worked", method="keywords")]},
    )
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]

    result = build_pipeline(gateway, db, settings, limiter).run_stream(stream)

    assert result["new"] == 0
    assert gateway.fetch_calls == []  # ни одного запроса к Telegram


def test_budget_stops_run(db, settings, limiter, crypto_stream):
    snapshots = {
        f"chan_{i}": make_snapshot(username=f"chan_{i}", channel_id=900 + i, subscribers=20000)
        for i in range(5)
    }
    gateway = FakeGateway(
        snapshots=snapshots,
        keyword_hits={
            "трейдинг": [Candidate(username=f"chan_{i}", method="keywords") for i in range(5)]
        },
    )
    settings.max_channels_per_run = 2
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]

    result = build_pipeline(gateway, db, settings, limiter).run_stream(stream)

    assert result["status"] == RUN_STATUS_BUDGET
    assert result["new"] == 2
    assert len(gateway.fetch_calls) == 2


def test_floodwait_streak_aborts_run_and_is_recorded(db, settings, limiter, crypto_stream):
    """Раздел 9.4: при двух FloodWait подряд прогон останавливается со сводкой."""

    class FloodingGateway(FakeGateway):
        def search_keyword(self, keyword, limit=30):
            raise RunAborted("2 FloodWait подряд — остановлен по лимитам")

    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]

    result = build_pipeline(FloodingGateway(), db, settings, limiter).run_stream(stream)

    assert result["status"] == RUN_STATUS_LIMITED
    assert result["aborted"] is True
    assert "FloodWait" in result["note"]
    assert db.stats()["recent_runs"][0]["status"] == RUN_STATUS_LIMITED


def test_methods_are_separated_by_a_pause(db, settings, limiter, crypto_stream):
    pauses: list[tuple[float, float]] = []
    limiter.pause_between_methods = lambda a, b: pauses.append((a, b))

    gateway = FakeGateway(keyword_hits={"трейдинг": []})
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords", "similar", "mentions"]
    pipeline = build_pipeline(gateway, db, settings, limiter)
    pipeline._seeds = lambda _s: ["@seed"]

    pipeline.run_stream(stream)
    # пауза ставится между методами, но не перед первым
    assert len(pauses) == 2


def test_similar_wins_over_keywords_as_source(db, settings, limiter, crypto_stream):
    """Канал, найденный двумя методами, считается similar — это даёт +25."""
    snapshot = make_snapshot(username="both_ways", channel_id=1101, subscribers=20000)
    gateway = FakeGateway(
        snapshots={"both_ways": snapshot},
        keyword_hits={"трейдинг": [Candidate(username="both_ways", method="keywords")]},
        similar_hits={"partner": [Candidate(username="both_ways", method="similar",
                                            source_channel="@partner")]},
    )
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords", "similar"]
    pipeline = build_pipeline(gateway, db, settings, limiter)
    pipeline._seeds = lambda _s: ["@partner"]

    pipeline.run_stream(stream)
    assert db.find(username="both_ways")["method"] == "similar"


def test_dead_channel_is_stored_as_filtered(db, settings, limiter, crypto_stream):
    dead = make_snapshot(
        username="dead_one",
        channel_id=1201,
        subscribers=20000,
        has_linked_chat=False,
        posts=make_posts(count=2, views=10, reactions=0, start_days_ago=90),
    )
    gateway = FakeGateway(
        snapshots={"dead_one": dead},
        keyword_hits={"трейдинг": [Candidate(username="dead_one", method="keywords")]},
    )
    stream = crypto_stream
    stream.keywords = ["трейдинг"]
    stream.methods = ["keywords"]

    build_pipeline(gateway, db, settings, limiter).run_stream(stream)

    row = db.find(username="dead_one")
    assert row["passed_filters"] == 0
    assert row["status"] == "filtered"
    assert "последний пост" in row["filter_reasons"]
    assert db.export_rows() == []


def test_rescan_updates_stale_metrics(db, settings, limiter, streams):
    snapshot = make_snapshot(username="old_chan", channel_id=1301, subscribers=20000)
    gateway = FakeGateway(snapshots={"old_chan": snapshot})
    pipeline = build_pipeline(gateway, db, settings, limiter)

    db.upsert_channel(
        {"channel_id": 1301, "username": "old_chan", "subscribers": 100,
         "method": "keywords", "link": "https://t.me/old_chan"},
        10, "", "crypto_core", True, [],
    )
    db.conn.execute("UPDATE channels SET last_checked = '2020-01-01T00:00:00+00:00'")
    db.conn.commit()

    result = pipeline.rescan_stale(streams, older_than_days=30)

    assert result["updated"] == 1
    assert db.find(username="old_chan")["subscribers"] == 20000
    assert db.stats()["total"] == 1  # пересканирование не плодит дублей


def test_channel_without_username_is_skipped(db, settings, limiter, crypto_stream):
    gateway = FakeGateway()
    pipeline = build_pipeline(gateway, db, settings, limiter)
    result = pipeline.process_candidate(
        Candidate(username=None, channel_id=42, method="mentions"), crypto_stream, []
    )
    assert result is None
    assert gateway.fetch_calls == []
