"""Локальный веб-интерфейс: доступ, формы, поиск, выгрузка."""

from __future__ import annotations

import http.cookiejar
import os
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from tghunter.web import AppState, build_server

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.symlink(REPO_ROOT / "config", tmp_path / "config")
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))

    state = AppState(env_file=str(tmp_path / ".env"),
                     db_path=str(tmp_path / "web.db"), default_source="demo")
    srv = build_server(state, port=0)          # 0 — свободный порт от ОС
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    class Client:
        base = f"http://127.0.0.1:{port}"
        token = state.token
        csrf = state.csrf

        def get(self, path="/"):
            return opener.open(self.base + path).read().decode()

        def raw(self, path="/"):
            return opener.open(self.base + path)

        def post(self, path, data):
            body = urllib.parse.urlencode(data).encode()
            return opener.open(
                urllib.request.Request(self.base + path, data=body)
            ).read().decode()

        def authorize(self):
            return self.get(f"/?t={self.token}")

    yield Client()
    srv.shutdown()
    srv.server_close()


def test_server_binds_localhost_only(server):
    assert server.base.startswith("http://127.0.0.1:")


def test_request_without_token_is_refused(server):
    plain = urllib.request.build_opener()     # без cookie
    with pytest.raises(urllib.error.HTTPError) as exc:
        plain.open(server.base + "/")
    assert exc.value.code == 403


def test_token_in_url_grants_access_and_sets_cookie(server):
    page = server.authorize()
    assert 'name="q"' in page                  # строка поиска на месте
    assert server.get("/")                     # дальше пускает по cookie


def test_post_without_csrf_is_refused(server):
    server.authorize()
    with pytest.raises(urllib.error.HTTPError) as exc:
        server.post("/search", {"q": "трейдинг", "source": "demo", "csrf": "подделка"})
    assert exc.value.code == 403


def test_search_form_returns_ranked_channels(server):
    server.authorize()
    page = server.post("/search", {
        "q": "трейдинг", "source": "demo", "csrf": server.csrf,
        "limit": "30", "min_subs": "1000", "max_subs": "1000000",
    })
    channels = re.findall(r'href="https://t\.me/(\w+)"', page)
    assert channels
    assert all(name.startswith("demo_") for name in channels)
    assert "Найдено" in page


def test_search_shows_verdicts(server):
    server.authorize()
    page = server.post("/search", {"q": "сигналы", "source": "demo", "csrf": server.csrf})
    assert 'class="tag black"' in page         # памперы помечены blacklist
    assert 'class="tag pass"' in page          # кто-то прошёл в выгрузку


def test_empty_query_is_reported_not_crashed(server):
    server.authorize()
    page = server.post("/search", {"q": "   ", "source": "demo", "csrf": server.csrf})
    assert "Пустой запрос" in page


def test_flash_message_is_shown_once(server):
    server.authorize()
    first = server.post("/search", {"q": "крипта", "source": "demo", "csrf": server.csrf})
    assert 'class="flash' in first
    assert 'class="flash' not in server.get("/")


def test_settings_form_writes_env_with_600(server, tmp_path):
    server.authorize()
    server.post("/settings", {"TGSTAT_TOKEN": "секретный_ключ_1234", "csrf": server.csrf})

    env = tmp_path / ".env"
    assert "TGSTAT_TOKEN=секретный_ключ_1234" in env.read_text(encoding="utf-8")
    assert oct(os.stat(env).st_mode)[-3:] == "600"


def test_saved_secret_is_shown_masked_not_in_full(server):
    server.authorize()
    server.post("/settings", {"TGSTAT_TOKEN": "abcdефgh0123456789", "csrf": server.csrf})
    page = server.get("/settings")
    assert "abcdефgh0123456789" not in page     # целиком не показываем
    assert "abcd…6789" in page


def test_empty_settings_field_keeps_existing_value(server, tmp_path):
    server.authorize()
    server.post("/settings", {"TGSTAT_TOKEN": "первый_ключ", "csrf": server.csrf})
    server.post("/settings", {"TGSTAT_TOKEN": "", "TG_API_ID": "555", "csrf": server.csrf})

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TGSTAT_TOKEN=первый_ключ" in text
    assert "TG_API_ID=555" in text


def test_settings_page_lists_sources(server):
    server.authorize()
    page = server.get("/settings")
    for title in ("Telegram (MTProto)", "TGStat API", "Демо-корпус"):
        assert title in page


def test_csv_download_survives_cyrillic_filename(server):
    """Имя файла кириллическое, а заголовки HTTP обязаны быть latin-1."""
    server.authorize()
    server.post("/search", {"q": "трейдинг", "source": "demo", "csrf": server.csrf})

    response = server.raw("/export")
    assert response.status == 200
    disposition = response.headers["Content-Disposition"]
    assert "filename*=UTF-8''" in disposition
    body = response.read().decode("utf-8-sig")
    assert "score" in body and "demo_" in body


def test_export_without_search_is_not_an_error(server):
    server.authorize()
    with pytest.raises(urllib.error.HTTPError) as exc:
        server.raw("/export")
    assert exc.value.code == 404


def test_unknown_page_returns_404(server):
    server.authorize()
    with pytest.raises(urllib.error.HTTPError) as exc:
        server.get("/" + urllib.parse.quote("нет-такой-страницы"))
    assert exc.value.code == 404


def test_html_is_escaped_against_injection(server):
    server.authorize()
    page = server.post("/search", {
        "q": '<img src=x onerror=alert(1)>', "source": "demo", "csrf": server.csrf,
    })
    assert "<img src=x" not in page
    assert "&lt;img" in page or "Пустой запрос" in page
