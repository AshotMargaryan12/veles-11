"""Вебхук Wazzup и команды CLI (разделы 3.1 и 8 ТЗ)."""

from __future__ import annotations

import json

import pytest

from crmai import cli
from crmai.service import build_services

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from crmai.webhook import create_app  # noqa: E402

EVENT = {
    "messages": [
        {
            "messageId": "w1",
            "chatId": "c1",
            "chatType": "telegram",
            "isEcho": True,
            "dateTime": "2026-09-21T09:00:00.000Z",
            "text": "гарантируем доход без рисков",
            "contact": {"name": "Блогер"},
        }
    ]
}


class SilentAmo:
    """Заглушка amo: в сеть не ходит, сделок не знает."""

    def search_contacts(self, query):
        return []

    def contact_leads(self, contact_id):
        return []

    def leads_by_ids(self, lead_ids):
        return []


class SilentNotifier:
    """Заглушка Telegram: складывает сообщения в список вместо отправки."""

    def __init__(self):
        self.messages = []

    def broadcast(self, text):
        self.messages.append(text)
        return 1

    def send(self, chat_id, text):
        return bool(self.broadcast(text))


def make_services(settings, db):
    return build_services(settings, db=db, llm=object(), amo=SilentAmo(), notifier=SilentNotifier())


@pytest.fixture
def client(crm_settings, crm_db):
    services = make_services(crm_settings, crm_db)
    return TestClient(create_app(services=services, settings=crm_settings)), services


def test_webhook_accepts_event_and_stores_it(client):
    http, services = client
    response = http.post("/webhook/wazzup", json=EVENT)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    # обработка идёт фоном, TestClient дожидается фоновых задач
    assert services.db.stats()["messages"] == 1


def test_webhook_deduplicates_retries(client):
    http, services = client
    http.post("/webhook/wazzup", json=EVENT)
    http.post("/webhook/wazzup", json=EVENT)

    assert services.db.stats()["messages"] == 1


def test_webhook_answers_200_on_broken_body(client):
    http, _ = client
    response = http.post("/webhook/wazzup", content="не json".encode("utf-8"))

    assert response.status_code == 200
    assert response.json()["ignored"] is True


def test_webhook_ping_is_accepted(client):
    http, services = client
    assert http.post("/webhook/wazzup", json={"test": True}).status_code == 200
    assert services.db.stats()["messages"] == 0


def test_webhook_secret_is_enforced(crm_settings, crm_db):
    crm_settings.wazzup_webhook_secret = "s3cret"
    services = make_services(crm_settings, crm_db)
    http = TestClient(create_app(services=services, settings=crm_settings))

    assert http.post("/webhook/wazzup", json=EVENT).status_code == 403
    assert http.post(
        "/webhook/wazzup", json=EVENT, headers={"X-Webhook-Secret": "s3cret"}
    ).status_code == 200


def test_health_reports_components(client):
    http, _ = client
    payload = http.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["amo"] is True
    assert payload["llm"] is True
    assert payload["messages"] == 0


# --- CLI ---------------------------------------------------------------------


def test_cli_check_reports_violation(capsys):
    assert cli.main(["check", "гарантируем", "доход"]) == 0
    assert "guaranteed_income" in capsys.readouterr().out


def test_cli_check_clean_text(capsys):
    assert cli.main(["check", "пришлю бриф завтра"]) == 0
    assert "не найдено" in capsys.readouterr().out


def test_cli_rules_lists_patterns(capsys):
    assert cli.main(["rules"]) == 0
    assert "branded_bot" in capsys.readouterr().out


def test_cli_config_check_reports_missing(capsys, tmp_path):
    env = tmp_path / "empty.env"
    env.write_text("", encoding="utf-8")

    assert cli.main(["--env", str(env), "config-check"]) == 1
    assert "Не заданы:" in capsys.readouterr().out


def test_cli_ingest_and_stats(tmp_path, capsys):
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps(EVENT), encoding="utf-8")
    db = str(tmp_path / "cli.db")
    env = tmp_path / "empty.env"
    env.write_text("", encoding="utf-8")

    assert cli.main(["--env", str(env), "--db", db, "ingest", str(payload)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["stored"] == 1
    assert "guaranteed_income" in result["alerts"]

    assert cli.main(["--env", str(env), "--db", db, "stats"]) == 0
    assert "messages" in capsys.readouterr().out


def test_cli_digest_prints_day(tmp_path, capsys):
    env = tmp_path / "empty.env"
    env.write_text("", encoding="utf-8")

    assert cli.main([
        "--env", str(env), "--db", str(tmp_path / "d.db"),
        "digest", "--date", "2026-09-21",
    ]) == 0
    assert "Дайджест за 21.09.2026" in capsys.readouterr().out


def test_cli_demo_runs_offline(capsys):
    assert cli.main(["demo"]) == 0
    output = capsys.readouterr().out
    assert "Статус на" in output
    assert "Дайджест" in output
