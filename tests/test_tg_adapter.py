"""Нормализация объектов Telethon и сквозной прогон команды `run`.

Сам Telethon здесь не нужен: его импорт в tghunter.tg отложен внутрь функций,
а разбор сообщения проверяется на фейковых объектах.
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import FakeGateway, make_posts, make_snapshot
from tghunter.models import Candidate
from tghunter.tg import _to_post


class FakeReaction:
    def __init__(self, count):
        self.count = count


class FakeReactions:
    def __init__(self, counts):
        self.results = [FakeReaction(c) for c in counts]


class FakeFromId:
    def __init__(self, channel_id):
        self.channel_id = channel_id


class FakeFwd:
    def __init__(self, channel_id=None, from_name=None):
        self.from_id = FakeFromId(channel_id) if channel_id else None
        self.from_name = from_name


class FakeMessage:
    def __init__(self, id=1, date=None, message="", views=None, reactions=None, fwd_from=None):
        self.id = id
        self.date = date or datetime(2026, 9, 21, tzinfo=timezone.utc)
        self.message = message
        self.views = views
        self.reactions = reactions
        self.fwd_from = fwd_from


def test_message_is_normalized():
    post = _to_post(
        FakeMessage(id=42, message="Текст с https://t.me/other", views=1500,
                    reactions=FakeReactions([10, 5, 2]))
    )
    assert post.id == 42
    assert post.views == 1500
    assert post.reactions == 17  # реакции всех типов суммируются
    assert post.urls == ["https://t.me/other"]


def test_message_without_views_or_reactions():
    post = _to_post(FakeMessage())
    assert post.views is None
    assert post.reactions == 0
    assert post.text == ""


def test_forward_source_is_captured():
    post = _to_post(FakeMessage(fwd_from=FakeFwd(channel_id=555, from_name="Канал")))
    assert post.forward_from_channel_id == 555
    assert post.forward_from_username == "Канал"


def test_naive_date_is_treated_as_utc():
    post = _to_post(FakeMessage(date=datetime(2026, 9, 21, 12, 0)))
    assert post.date.tzinfo is timezone.utc


def test_run_command_end_to_end(tmp_path, monkeypatch, capsys):
    """`run --stream` ходит в Telegram, пишет базу и кладёт CSV в exports/."""
    import tghunter.cli as cli

    snapshot = make_snapshot(
        username="e2e_channel",
        channel_id=4242,
        subscribers=20000,
        about="Крипта и трейдинг. По рекламе: @adv_manager",
        posts=make_posts(count=15, views=2500, reactions=40,
                         text="Обзор рынка криптовалют #реклама erid 2Vfn"),
    )
    gateway = FakeGateway(
        snapshots={"e2e_channel": snapshot},
        keyword_hits={
            "трейдинг": [Candidate(username="e2e_channel", method="keywords")]
        },
    )
    monkeypatch.setattr(cli, "_gateway", lambda settings, limiter, demo=False: gateway)
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("METHOD_PAUSE_MIN", "0")
    monkeypatch.setenv("METHOD_PAUSE_MAX", "0")
    monkeypatch.setenv("RATE_MIN_INTERVAL", "0")
    monkeypatch.setenv("RATE_MAX_INTERVAL", "0")

    streams = cli.load_streams("config/streams.yaml")
    streams["crypto_core"].keywords = ["трейдинг"]
    streams["crypto_core"].methods = ["keywords"]
    monkeypatch.setattr(cli, "_streams", lambda settings: streams)

    db_path = tmp_path / "e2e.db"
    code = cli.main(["--db", str(db_path), "run", "--stream", "crypto_core"])

    assert code == 0
    out = capsys.readouterr().out
    assert "новых 1" in out
    assert "CSV [crypto_core]" in out

    csv_files = list((tmp_path / "exports").rglob("crypto_core.csv"))
    assert len(csv_files) == 1
    content = csv_files[0].read_text(encoding="utf-8-sig")
    assert "e2e_channel" in content
    assert "@adv_manager" in content

    from tghunter.db import Database

    with Database(db_path) as db:
        row = db.find(username="e2e_channel")
        assert row["status"] == "exported"  # после выгрузки канал помечен


def test_run_all_stops_after_limit_abort(tmp_path, monkeypatch, capsys):
    """При остановке по лимитам остальные стримы не запускаются, код выхода 2."""
    import tghunter.cli as cli
    from tghunter.ratelimit import RunAborted

    class FloodingGateway(FakeGateway):
        def search_keyword(self, keyword, limit=30):
            raise RunAborted("2 FloodWait подряд — остановлен по лимитам")

        def similar_channels(self, username, limit=50):
            raise RunAborted("2 FloodWait подряд — остановлен по лимитам")

    monkeypatch.setattr(cli, "_gateway", lambda s, l, demo=False: FloodingGateway())
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("METHOD_PAUSE_MIN", "0")
    monkeypatch.setenv("METHOD_PAUSE_MAX", "0")
    monkeypatch.setenv("RATE_MIN_INTERVAL", "0")
    monkeypatch.setenv("RATE_MAX_INTERVAL", "0")

    code = cli.main(["--db", str(tmp_path / "abort.db"), "run", "--all", "--no-export"])
    assert code == 2

    from tghunter.db import Database
    from tghunter.config import load_streams

    enabled = [s.name for s in load_streams("config/streams.yaml").values() if s.enabled]
    with Database(tmp_path / "abort.db") as db:
        runs = db.stats()["recent_runs"]
        # прогон остановился на первом же упавшем стриме, остальные не запускались
        assert runs[0]["status"] == "aborted_by_limits"
        assert len(runs) < len(enabled)
        started = {r["stream"] for r in runs}
        aborted_at = enabled.index(runs[0]["stream"])
        assert not started & set(enabled[aborted_at + 1:])
