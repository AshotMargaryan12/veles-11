"""База, дедуп и импорт существующей базы (раздел 7 ТЗ)."""

from __future__ import annotations

from tghunter.db import normalize_username
from tghunter.models import STATUS_BLACKLIST, STATUS_EXPORTED, STATUS_NO_CONTACT
import pytest


def metrics(channel_id=1, username="chan", **overrides):
    base = {
        "channel_id": channel_id,
        "username": username,
        "title": "Канал",
        "about": "описание",
        "link": f"https://t.me/{username}",
        "subscribers": 5000,
        "er": 6.0,
        "median_views": 300,
        "avg_reactions": 8,
        "comments_enabled": True,
        "days_since_last_post": 1,
        "posts_last_30d": 12,
        "language": "ru",
        "monetized": True,
        "monetization_markers": ["#реклама"],
        "contact": "@adv",
        "blacklist_hit": False,
        "blacklist_markers": [],
        "method": "similar",
        "source_channel": "@seed",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("@Foo_bar", "Foo_bar"),
        ("https://t.me/baz", "baz"),
        ("http://t.me/baz?start=1", "baz"),
        ("t.me/qux/123", "qux"),
        ("telegram.me/quux", "quux"),
        ("plain_name", "plain_name"),
        ("# comment", None),
        ("bad name!", None),
        ("", None),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


def test_insert_then_update_does_not_duplicate(db):
    cid, is_new = db.upsert_channel(metrics(), 80, "x", "crypto_core", True, [])
    assert is_new is True
    cid2, is_new2 = db.upsert_channel(metrics(subscribers=9000), 85, "y", "crypto_core", True, [])
    assert (cid2, is_new2) == (cid, False)
    assert db.stats()["total"] == 1
    row = db.find(channel_id=1)
    assert row["subscribers"] == 9000  # метрики обновились
    assert row["score"] == 85


def test_dedup_by_username_when_id_changes(db):
    db.upsert_channel(metrics(channel_id=1, username="samename"), 50, "", "s", True, [])
    _cid, is_new = db.upsert_channel(
        metrics(channel_id=1, username="SameName"), 60, "", "s", True, []
    )
    assert is_new is False
    assert db.stats()["total"] == 1


def test_known_keys_covers_username_and_id(db):
    db.upsert_channel(metrics(channel_id=7, username="abc"), 10, "", "s", True, [])
    keys = db.known_keys()
    assert "abc" in keys and "id:7" in keys


def test_status_blacklist(db):
    db.upsert_channel(
        metrics(blacklist_hit=True, blacklist_markers=["памп"]), 0, "", "s", False,
        ["blacklist_hit: памп"],
    )
    row = db.find(channel_id=1)
    assert row["status"] == STATUS_BLACKLIST
    assert row["blacklist_markers"] == "памп"
    # остаётся в базе, чтобы не проверять повторно
    assert db.exists(channel_id=1)
    # но не попадает в выгрузку
    assert db.export_rows() == []


def test_status_no_contact(db):
    db.upsert_channel(metrics(contact=None), 40, "", "s", True, [])
    assert db.find(channel_id=1)["status"] == STATUS_NO_CONTACT


def test_filtered_channel_is_stored_but_not_exported(db):
    db.upsert_channel(metrics(), 20, "", "s", False, ["er 1.0 < 3.0"])
    assert db.find(channel_id=1)["status"] == "filtered"
    assert db.export_rows() == []
    assert db.stats()["total"] == 1


def test_exported_channel_stays_exported_after_recheck(db):
    """Повторная проверка не возвращает выгруженный канал в «новые»."""
    cid, _ = db.upsert_channel(metrics(), 70, "", "s", True, [])
    db.mark_exported([cid])
    assert db.find(channel_id=cid)["status"] == STATUS_EXPORTED

    db.upsert_channel(metrics(subscribers=6000), 75, "", "s", True, [])
    assert db.find(channel_id=cid)["status"] == STATUS_EXPORTED
    assert db.export_rows(new_only=True) == []


def test_export_rows_sorted_by_score(db):
    db.upsert_channel(metrics(1, "low"), 30, "", "s", True, [])
    db.upsert_channel(metrics(2, "high"), 90, "", "s", True, [])
    db.upsert_channel(metrics(3, "mid"), 60, "", "s", True, [])
    assert [r["username"] for r in db.export_rows()] == ["high", "mid", "low"]


def test_export_rows_filters(db):
    db.upsert_channel(metrics(1, "a"), 30, "", "crypto_core", True, [])
    db.upsert_channel(metrics(2, "b"), 90, "", "signals", True, [])
    assert len(db.export_rows(stream="signals")) == 1
    assert len(db.export_rows(min_score=50)) == 1
    assert len(db.export_rows(only_no_contact=True)) == 0


def test_import_existing_prevents_rediscovery(db):
    added, skipped = db.import_existing(["@crm_chan", "https://t.me/other", "@crm_chan"])
    assert (added, skipped) == (2, 1)
    assert "crm_chan" in db.known_keys()
    # импортированные не идут в выгрузку
    assert db.export_rows() == []


def test_import_existing_skips_channels_already_found(db):
    db.upsert_channel(metrics(username="already"), 50, "", "s", True, [])
    added, skipped = db.import_existing(["@already"])
    assert (added, skipped) == (0, 1)


def test_stale_channels(db):
    db.upsert_channel(metrics(), 50, "", "s", True, [])
    assert db.stale_channels(older_than_days=30) == []
    db.conn.execute("UPDATE channels SET last_checked = '2020-01-01T00:00:00+00:00'")
    db.conn.commit()
    assert len(db.stale_channels(older_than_days=30)) == 1


def test_runs_history(db):
    run_id = db.start_run("crypto_core")
    db.log_candidate(run_id, "some_chan", "similar", "@seed")
    db.finish_run(run_id, "ok", checked=10, new_channels=4)
    recent = db.stats()["recent_runs"]
    assert recent[0]["stream"] == "crypto_core"
    assert recent[0]["new_channels"] == 4
    assert recent[0]["status"] == "ok"


def test_stats_counts(db):
    db.upsert_channel(metrics(1, "a"), 50, "", "crypto_core", True, [])
    db.upsert_channel(metrics(2, "b", contact=None), 40, "", "crypto_core", True, [])
    db.upsert_channel(metrics(3, "c", blacklist_hit=True, blacklist_markers=["памп"]),
                      0, "", "signals", False, ["blacklist_hit: памп"])
    stats = db.stats()
    assert stats["total"] == 3
    assert stats["blacklisted"] == 1
    assert stats["no_contact"] == 1
    assert stats["exportable"] == 2
    assert stats["by_stream"]["crypto_core"] == 2
    assert stats["new_last_week"] == 3
