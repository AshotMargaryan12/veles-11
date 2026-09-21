"""Хранилище анализатора: активность чатов, кэш матчинга, бюджет LLM."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from crmai.models import DIRECTION_IN, DIRECTION_OUT, Message


def message(mid="m1", chat="c1", direction=DIRECTION_IN, text="привет", ts=None, **kw):
    return Message(
        message_id=mid,
        chat_id=chat,
        direction=direction,
        text=text,
        ts=ts or "2026-09-21T09:00:00+00:00",
        **kw,
    )


def test_chat_is_created_and_updated_from_messages(crm_db):
    crm_db.add_message(message(contact_name="Блогер"))
    crm_db.add_message(message(mid="m2", ts="2026-09-21T10:00:00+00:00", contact_username="bloger"))

    chat = crm_db.get_chat("c1")
    assert chat["contact_name"] == "Блогер"
    assert chat["contact_username"] == "bloger"
    assert chat["last_message_ts"] == "2026-09-21T10:00:00+00:00"


def test_chats_with_activity_respects_last_analyzed(crm_db):
    crm_db.add_message(message())
    assert len(crm_db.chats_with_activity()) == 1

    crm_db.mark_analyzed("c1", "2026-09-21T09:00:00+00:00")
    assert crm_db.chats_with_activity() == []

    crm_db.add_message(message(mid="m2", ts="2026-09-21T11:00:00+00:00"))
    assert len(crm_db.chats_with_activity()) == 1


def test_messages_for_chat_keeps_chronology_and_limit(crm_db):
    for index in range(5):
        crm_db.add_message(message(mid=f"m{index}", ts=f"2026-09-21T0{index}:00:00+00:00"))
    rows = crm_db.messages_for_chat("c1", limit=3)
    assert [row["message_id"] for row in rows] == ["m2", "m3", "m4"]


def test_outgoing_for_day_filters_direction_and_window(crm_db):
    crm_db.add_message(message(mid="in", direction=DIRECTION_IN, ts="2026-09-21T09:00:00+00:00"))
    crm_db.add_message(message(mid="out", direction=DIRECTION_OUT, ts="2026-09-21T10:00:00+00:00"))
    crm_db.add_message(message(mid="old", direction=DIRECTION_OUT, ts="2026-09-19T10:00:00+00:00"))

    rows = crm_db.outgoing_for_day("c1", "2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00")
    assert [row["message_id"] for row in rows] == ["out"]


def test_messages_before_gives_context(crm_db):
    for index in range(8):
        crm_db.add_message(message(mid=f"m{index}", ts=f"2026-09-21T0{index}:00:00+00:00"))
    rows = crm_db.messages_before("c1", "2026-09-21T07:00:00+00:00", 5)
    assert [row["message_id"] for row in rows] == ["m2", "m3", "m4", "m5", "m6"]


def test_match_cache_expires(crm_db):
    crm_db.add_message(message())
    crm_db.set_match("c1", 555, 111)
    chat = crm_db.get_chat("c1")
    assert crm_db.match_is_fresh(chat) is True
    assert chat["amo_lead_id"] == 555

    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    crm_db.conn.execute("UPDATE chats SET matched_at = ? WHERE chat_id = 'c1'", (stale,))
    crm_db.conn.commit()
    assert crm_db.match_is_fresh(crm_db.get_chat("c1")) is False


def test_unmatched_count_only_counts_active_chats(crm_db):
    crm_db.add_message(message(chat="c1"))
    crm_db.add_message(message(mid="m2", chat="c2"))
    crm_db.set_match("c1", 555)

    bounds = ("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00")
    assert crm_db.unmatched_count(*bounds) == 1
    assert crm_db.unmatched_count("2026-09-25T00:00:00+00:00", "2026-09-26T00:00:00+00:00") == 0


def test_alerts_are_deduplicated(crm_db):
    assert crm_db.register_alert("m1", "c1", "no_risk", "без рисков") is True
    assert crm_db.register_alert("m1", "c1", "no_risk", "без рисков") is False
    assert crm_db.register_alert("m1", "c1", "guaranteed_income", "гарантия") is True


def test_llm_budget_counter(crm_db):
    assert crm_db.llm_calls_today("2026-09-21") == 0
    crm_db.bump_llm_calls("2026-09-21")
    crm_db.bump_llm_calls("2026-09-21", 4)
    assert crm_db.llm_calls_today("2026-09-21") == 5
    assert crm_db.llm_calls_today("2026-09-22") == 0


def test_last_analysis_returns_latest(crm_db):
    crm_db.save_analysis("c1", 1, {"stage": "первый"})
    crm_db.save_analysis("c1", 1, {"stage": "второй"})
    assert crm_db.last_analysis("c1")["stage"] == "второй"
    assert crm_db.last_analysis("unknown") is None


def test_analyses_since_gives_one_row_per_chat(crm_db):
    crm_db.save_analysis("c1", 1, {"stage": "старый"})
    crm_db.save_analysis("c1", 1, {"stage": "новый"})
    crm_db.save_analysis("c2", 2, {"stage": "другой"})
    rows = crm_db.analyses_since("2000-01-01T00:00:00+00:00")
    assert {row["chat_id"]: row["summary"]["stage"] for row in rows} == {
        "c1": "новый",
        "c2": "другой",
    }


def test_failures_are_recorded_per_day(crm_db):
    crm_db.record_failure("2026-09-21", "status_llm", "c1", "таймаут")
    assert len(crm_db.failures_for_day("2026-09-21")) == 1
    assert crm_db.failures_for_day("2026-09-22") == []


def test_meta_roundtrip(crm_db):
    crm_db.set_meta("day_note:2026-09-21", "день так себе")
    crm_db.set_meta("day_note:2026-09-21", "день норм")
    assert crm_db.get_meta("day_note:2026-09-21") == "день норм"
    assert crm_db.get_meta("нет такого", "по умолчанию") == "по умолчанию"
