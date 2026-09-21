"""Клиент amoCRM и матчинг чат -> сделка (раздел 3.2 ТЗ)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crmai.amo import AmoAuthError, AmoClient, AmoError, is_open_lead, pick_lead, tomorrow_noon
from crmai.matching import resolve_lead, search_queries
from crmai.models import Message

from conftest import FakeHttp, FakeResponse


def lead(lead_id=1, pipeline=7, status=11, updated=100, **kw):
    base = {"id": lead_id, "pipeline_id": pipeline, "status_id": status, "updated_at": updated}
    base.update(kw)
    return base


def test_open_lead_rules():
    assert is_open_lead(lead(), [7]) is True
    assert is_open_lead(lead(status=142), [7]) is False          # успешно реализовано
    assert is_open_lead(lead(status=143), [7]) is False          # закрыто с отказом
    assert is_open_lead(lead(closed_at=123), [7]) is False
    assert is_open_lead(lead(is_deleted=True), [7]) is False
    assert is_open_lead(lead(pipeline=9), [7]) is False          # чужая воронка


def test_pick_latest_open_lead():
    leads = [lead(1, updated=100), lead(2, updated=500), lead(3, pipeline=9, updated=900)]
    assert pick_lead(leads, [7])["id"] == 2
    assert pick_lead([lead(4, status=142)], [7]) is None


def test_tomorrow_noon():
    from datetime import datetime

    ts = tomorrow_noon(now=datetime(2026, 9, 21, 19, 30))
    assert datetime.fromtimestamp(ts) == datetime(2026, 9, 22, 12, 0)


def test_add_note_posts_common_type(crm_settings):
    http = FakeHttp({("POST", "/notes"): FakeResponse(200, {"_embedded": {"notes": [{"id": 42}]}})})
    client = AmoClient(crm_settings, session=http)

    assert client.add_note(15, "текст") == 42
    call = http.calls[0]
    assert call["url"].endswith("/api/v4/leads/15/notes")
    assert call["json"][0]["note_type"] == "common"
    assert call["json"][0]["params"]["text"] == "текст"


def test_create_task_binds_to_lead_and_manager(crm_settings):
    http = FakeHttp({("POST", "/tasks"): FakeResponse(200, {"_embedded": {"tasks": [{"id": 9}]}})})
    client = AmoClient(crm_settings, session=http)

    client.create_task(15, "Определить следующий шаг", 1790000000, 77)
    task = http.calls[0]["json"][0]
    assert task["entity_type"] == "leads"
    assert task["entity_id"] == 15
    assert task["responsible_user_id"] == 77


def test_401_triggers_refresh_and_retry(crm_settings, tmp_path):
    http = FakeHttp(
        {
            ("GET", "/account"): [FakeResponse(401), FakeResponse(200, {"id": 1, "name": "Veles"})],
            ("POST", "/oauth2/access_token"): FakeResponse(
                200, {"access_token": "new-access", "refresh_token": "new-refresh"}
            ),
        }
    )
    client = AmoClient(crm_settings, session=http)

    assert client.whoami()["name"] == "Veles"
    assert client.access_token == "new-access"
    # новая пара токенов сохранена: refresh одноразовый
    saved = json.loads(Path(crm_settings.amo_token_file).read_text(encoding="utf-8"))
    assert saved == {"access_token": "new-access", "refresh_token": "new-refresh"}


def test_refresh_without_credentials_fails_loudly(crm_settings):
    crm_settings.amo_client_secret = ""
    client = AmoClient(crm_settings, session=FakeHttp({("GET", "/account"): FakeResponse(401)}))
    with pytest.raises(AmoAuthError):
        client.whoami()


def test_http_error_raises(crm_settings):
    client = AmoClient(crm_settings, session=FakeHttp({("GET", "/account"): FakeResponse(500)}))
    with pytest.raises(AmoError):
        client.whoami()


def test_204_means_nothing_found(crm_settings):
    client = AmoClient(crm_settings, session=FakeHttp({("GET", "/contacts"): FakeResponse(204)}))
    assert client.search_contacts("@bloger") == []


# --- матчинг ----------------------------------------------------------------


def add_chat(db, username="bloger", **kw):
    db.add_message(
        Message(
            message_id="m1",
            chat_id="c1",
            direction="in",
            text="привет",
            ts="2026-09-21T09:00:00+00:00",
            contact_name="Блогер",
            contact_username=username,
            **kw,
        )
    )
    return db.get_chat("c1")


def test_search_queries_order(crm_db):
    chat = add_chat(crm_db)
    assert search_queries(chat)[0] == "bloger"


def test_resolve_lead_uses_ids_from_wazzup(crm_db, crm_settings):
    chat = add_chat(crm_db, amo_contact_id=11, amo_lead_id=99)
    http = FakeHttp()
    match = resolve_lead(crm_db, AmoClient(crm_settings, session=http), chat, crm_settings)

    assert (match.lead_id, match.source) == (99, "wazzup")
    assert http.calls == []  # в amo не ходили: id уже был в событии


def test_resolve_lead_by_contact_search(crm_db, crm_settings):
    chat = add_chat(crm_db)
    http = FakeHttp(
        {
            # ключи проверяются по порядку: сначала карточка контакта, потом поиск
            ("GET", "/contacts/11"): FakeResponse(
                200, {"id": 11, "_embedded": {"leads": [{"id": 1}, {"id": 2}]}}
            ),
            ("GET", "/contacts"): FakeResponse(200, {"_embedded": {"contacts": [{"id": 11}]}}),
            ("GET", "/leads"): FakeResponse(
                200, {"_embedded": {"leads": [lead(1, updated=100), lead(2, updated=300)]}}
            ),
        }
    )
    match = resolve_lead(crm_db, AmoClient(crm_settings, session=http), chat, crm_settings)

    assert match.lead_id == 2  # последняя по updated_at
    assert crm_db.get_chat("c1")["amo_lead_id"] == 2


def test_resolve_lead_marks_unmatched_when_no_contact(crm_db, crm_settings):
    chat = add_chat(crm_db)
    http = FakeHttp({("GET", "/contacts"): FakeResponse(204)})
    match = resolve_lead(crm_db, AmoClient(crm_settings, session=http), chat, crm_settings)

    assert match.matched is False
    assert "контакт не найден" in match.reason
    assert crm_db.get_chat("c1")["unmatched"] == 1


def test_resolve_lead_marks_unmatched_when_no_open_lead(crm_db, crm_settings):
    chat = add_chat(crm_db)
    http = FakeHttp(
        {
            ("GET", "/contacts/11"): FakeResponse(200, {"id": 11, "_embedded": {"leads": [{"id": 5}]}}),
            ("GET", "/contacts"): FakeResponse(200, {"_embedded": {"contacts": [{"id": 11}]}}),
            ("GET", "/leads"): FakeResponse(200, {"_embedded": {"leads": [lead(5, status=143)]}}),
        }
    )
    match = resolve_lead(crm_db, AmoClient(crm_settings, session=http), chat, crm_settings)

    assert match.matched is False
    assert match.contact_id == 11
    assert crm_db.get_chat("c1")["unmatched"] == 1


def test_fresh_cache_skips_amo(crm_db, crm_settings):
    add_chat(crm_db)
    crm_db.set_match("c1", 777, 11)
    http = FakeHttp()
    match = resolve_lead(crm_db, AmoClient(crm_settings, session=http), crm_db.get_chat("c1"), crm_settings)

    assert (match.lead_id, match.source) == (777, "cache")
    assert http.calls == []
