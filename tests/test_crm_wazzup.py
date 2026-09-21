"""Разбор вебхуков Wazzup и дедуп сообщений (раздел 3.1 ТЗ)."""

from __future__ import annotations

import pytest

from crmai.models import DIRECTION_IN, DIRECTION_OUT
from crmai.wazzup import detect_direction, extract_text, normalize_ts, parse_payload


def event(**overrides):
    base = {
        "messageId": "m1",
        "chatId": "tg-1",
        "chatType": "telegram",
        "text": "привет",
        "dateTime": "2026-09-21T09:00:00.000Z",
        "status": "inbound",
        "contact": {"name": "Блогер", "username": "@bloger"},
    }
    base.update(overrides)
    return base


def test_parse_envelope_with_messages():
    messages = parse_payload({"messages": [event(), event(messageId="m2")]})
    assert [m.message_id for m in messages] == ["m1", "m2"]
    assert messages[0].contact_username == "bloger"
    assert messages[0].chat_type == "telegram"


def test_parse_single_event_without_envelope():
    assert len(parse_payload(event())) == 1


def test_test_ping_is_not_a_message():
    assert parse_payload({"test": True}) == []


def test_event_without_ids_is_skipped():
    assert parse_payload({"messages": [{"text": "без id"}]}) == []


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"status": "inbound"}, DIRECTION_IN),
        ({"status": "delivered"}, DIRECTION_OUT),
        ({"isEcho": True}, DIRECTION_OUT),
        ({"isEcho": False}, DIRECTION_IN),
        ({"direction": "outgoing"}, DIRECTION_OUT),
        ({}, DIRECTION_IN),  # неизвестный формат — считаем входящим
    ],
)
def test_detect_direction(payload, expected):
    assert detect_direction(payload) == expected


def test_media_is_marked_and_not_transcribed():
    text, is_media = extract_text({"type": "audio", "contentUri": "https://x/a.ogg"})
    assert text == "[media] audio"
    assert is_media is True


def test_media_keeps_caption():
    text, is_media = extract_text({"type": "image", "contentUri": "u", "text": "вот макет"})
    assert text == "[media] image вот макет"
    assert is_media is True


def test_plain_text_is_not_media():
    assert extract_text({"type": "text", "text": "привет"}) == ("привет", False)


def test_whatsapp_phone_taken_from_chat_id():
    message = parse_payload(event(chatType="whatsapp", chatId="79161234567"))[0]
    assert message.contact_phone == "79161234567"


def test_amo_ids_from_event_are_used():
    message = parse_payload(event(contact={"name": "Б", "crmId": "55", "crmLeadId": "99"}))[0]
    assert (message.amo_contact_id, message.amo_lead_id) == (55, 99)


@pytest.mark.parametrize(
    "raw",
    ["2026-09-21T09:00:00.000Z", "2026-09-21T12:00:00+03:00", 1790000000, "1790000000"],
)
def test_normalize_ts_always_utc_iso(raw):
    assert normalize_ts(raw).endswith("+00:00")


def test_duplicate_message_id_is_ignored(crm_db):
    messages = parse_payload({"messages": [event(), event()]})
    assert crm_db.add_message(messages[0]) is True
    assert crm_db.add_message(messages[1]) is False
    assert crm_db.stats()["messages"] == 1
