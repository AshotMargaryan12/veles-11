"""Выгрузки: CSV, сводка в Telegram, amoCRM (раздел 8 ТЗ)."""

from __future__ import annotations

import csv
from pathlib import Path

from tghunter.config import Settings
from tghunter.exporters.amocrm import AmoClient
from tghunter.exporters.csv_export import export_csv
from tghunter.exporters.notify import build_summary, send_summary


def row(**overrides):
    base = {
        "username": "chan", "link": "https://t.me/chan", "title": "Канал",
        "subscribers": 10000, "er": 7.5, "median_views": 700, "avg_views": 750.0,
        "days_since_last_post": 1, "posts_last_30d": 12, "avg_reactions": 20.0,
        "comments_enabled": 1, "language": "ru", "monetized": 1,
        "monetization_markers": "#реклама", "contact": "@adv", "score": 90,
        "score_breakdown": "monetized+30", "method": "similar",
        "source_channel": "@seed", "stream": "crypto_core", "status": "new",
        "first_seen": "2026-09-20T10:00:00+00:00",
        "last_checked": "2026-09-21T10:00:00+00:00", "channel_id": 1,
    }
    base.update(overrides)
    return base


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def test_csv_has_all_required_columns(tmp_path):
    result = export_csv([row()], tmp_path / "out.csv")
    rows = read_csv(result["main_file"])
    assert result["main_count"] == 1

    headers = rows[0].keys()
    for required in ("username", "ссылка", "title", "подписчики", "ER_%",
                     "медиана_просмотров", "язык", "монетизация", "контакт",
                     "score", "источник"):
        assert required in headers


def test_csv_renders_booleans_readably(tmp_path):
    result = export_csv([row(monetized=0, comments_enabled=1)], tmp_path / "out.csv")
    record = read_csv(result["main_file"])[0]
    assert record["монетизация"] == "нет"
    assert record["комментарии"] == "да"


def test_no_contact_channels_go_to_separate_file(tmp_path):
    rows = [row(username="with_contact"), row(username="without", contact=None)]
    result = export_csv(rows, tmp_path / "stream.csv")

    assert result["main_count"] == 1
    assert result["no_contact_count"] == 1
    assert Path(result["no_contact_file"]).name == "stream_no_contact.csv"
    assert read_csv(result["main_file"])[0]["username"] == "with_contact"
    assert read_csv(result["no_contact_file"])[0]["username"] == "without"


def test_no_extra_file_when_all_have_contacts(tmp_path):
    result = export_csv([row()], tmp_path / "out.csv")
    assert result["no_contact_file"] is None


def test_export_creates_missing_directories(tmp_path):
    target = tmp_path / "exports" / "2026-09-21" / "crypto.csv"
    export_csv([row()], target)
    assert target.exists()


def test_summary_text_lists_totals_and_top(capsys):
    results = [
        {"stream": "crypto_core", "checked": 40, "new": 12},
        {"stream": "neighbors", "checked": 20, "new": 5},
    ]
    top = [row(username="best", score=95), row(username="second", score=80)]
    text = build_summary(results, top)

    assert "Найдено новых: <b>17</b>" in text
    assert "crypto_core: новых 12" in text
    assert "95 — @best" in text
    assert "остановлен" not in text


def test_summary_flags_aborted_run():
    text = build_summary([{"stream": "x", "checked": 1, "new": 0}], [], aborted=True)
    assert "остановлен по лимитам" in text


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, get_response=None, post_response=None):
        self.get_response = get_response or FakeResponse(204)
        self.post_response = post_response or FakeResponse(200)
        self.posts: list = []
        self.gets: list = []

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return self.get_response

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return self.post_response


def test_summary_not_sent_without_credentials():
    assert send_summary("", "", "текст") is False


def test_summary_sent_via_bot_api():
    http = FakeHttp()
    assert send_summary("TOKEN", "-100123", "текст", session=http) is True
    url, payload = http.posts[0]
    assert url.endswith("/botTOKEN/sendMessage")
    assert payload["chat_id"] == "-100123"
    assert payload["parse_mode"] == "HTML"


def test_summary_failure_does_not_raise():
    class Broken:
        def post(self, *a, **kw):
            raise RuntimeError("сеть недоступна")

    assert send_summary("T", "C", "текст", session=Broken()) is False


def amo_settings():
    return Settings(
        amo_base_url="https://example.amocrm.ru",
        amo_access_token="token",
        amo_pipeline_id="111",
        amo_status_id="222",
        amo_fields={"link": "1", "subscribers": "2", "er": "3",
                    "stream": "4", "score": "5"},
    )


def test_amo_lead_body_maps_fields():
    client = AmoClient(amo_settings(), session=FakeHttp())
    lead = client.build_lead(row())

    assert lead["name"] == "Канал"
    assert lead["pipeline_id"] == 111
    assert lead["status_id"] == 222
    values = {f["field_id"]: f["values"][0]["value"] for f in lead["custom_fields_values"]}
    assert values[1] == "https://t.me/chan"
    assert values[5] == "90"


def test_amo_dedups_by_channel_link():
    """Дедуп на стороне amo по ссылке канала."""
    existing = FakeResponse(200, {"_embedded": {"leads": [{"id": 5}]}})
    http = FakeHttp(get_response=existing)
    result = AmoClient(amo_settings(), session=http).create_leads([row()])

    assert result == {"created": 0, "skipped": 1, "total": 1}
    assert http.posts == []


def test_amo_creates_new_leads():
    http = FakeHttp(get_response=FakeResponse(204), post_response=FakeResponse(200))
    result = AmoClient(amo_settings(), session=http).create_leads([row(), row(username="b")])

    assert result["created"] == 2
    assert len(http.posts) == 1  # одна пачка
    assert len(http.posts[0][1]) == 2


def test_amo_export_skipped_when_not_configured():
    from tghunter.exporters.amocrm import export_to_amo

    assert export_to_amo(Settings(), [row()])["created"] == 0
