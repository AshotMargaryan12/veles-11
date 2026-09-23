"""Локальный веб-интерфейс: поля для ключей, строка поиска, выгрузка.

Поднимается ТОЛЬКО на 127.0.0.1 — наружу не смотрит. Ключи вводятся в форме и
ложатся в .env с правами 600 на этой же машине; никуда не отправляются.

Запуск:  python -m tghunter web
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

from .config import Settings, load_settings
from .credentials import FIELDS, apply_to_environ, masked_values, write_env
from .db import Database
from .exporters.csv_export import export_csv
from .pipeline import export_dir_for_today
from .ratelimit import RateLimiter
from .search import build_adhoc_stream, run_search, split_queries
from .sources import build_source, describe_sources

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COOKIE_NAME = "tghunter_token"

# Один процесс — одна поисковая операция: параллельные прогоны запрещены
_search_lock = threading.Lock()


class AppState:
    """Общее состояние сервера: настройки, токен доступа, последняя выдача."""

    def __init__(self, env_file: str = ".env", db_path: Optional[str] = None,
                 default_source: str = "demo"):
        self.env_file = env_file
        self.token = secrets.token_urlsafe(24)
        self.csrf = secrets.token_urlsafe(24)
        self.default_source = default_source
        self.db_path_override = db_path
        self.last_result: Optional[dict[str, Any]] = None
        self.last_csv: Optional[Path] = None
        self.flash: Optional[tuple[str, str]] = None

    def settings(self) -> Settings:
        settings = load_settings(self.env_file)
        if self.db_path_override:
            settings.db_path = self.db_path_override
        return settings


# --------------------------------------------------------------------------
# Разметка
# --------------------------------------------------------------------------

_CSS = """
:root{--bg:#f1f4f4;--surface:#fff;--surface2:#e9eeee;--ink:#131a1b;--muted:#5a6769;
--line:#d5dede;--accent:#0e6e68;--pass:#1f7a4d;--fail:#b23a25;--warn:#8a5d06;
--warnbg:#f6ead2;--passbg:#dcefe3;--failbg:#f6e0db;}
@media(prefers-color-scheme:dark){:root{--bg:#0d1213;--surface:#151d1e;--surface2:#1d2728;
--ink:#e4ebea;--muted:#8b9b9b;--line:#273435;--accent:#3fc7bc;--pass:#4fbf86;--fail:#e3705a;
--warn:#d9a43c;--warnbg:#2a2113;--passbg:#14291f;--failbg:#2e1a16;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif;padding:0 16px 56px}
.wrap{max-width:1080px;margin:0 auto}
/* Ссылки берут цвет из токенов: дефолтный синий нечитаем на тёмном фоне */
a{color:var(--accent);text-decoration-color:color-mix(in srgb,var(--accent) 45%,transparent)}
a:hover{text-decoration-thickness:2px}
a:visited{color:var(--accent)}
header{padding:28px 0 18px;border-bottom:1px solid var(--line);margin-bottom:24px;
display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
nav{display:flex;gap:14px;margin-left:auto}
nav a{color:var(--muted);text-decoration:none;font-size:14px;padding:4px 0;
border-bottom:2px solid transparent}
nav a.on{color:var(--accent);border-bottom-color:var(--accent)}
nav a:hover{color:var(--ink)}
h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
margin:0 0 4px;font-family:ui-monospace,monospace;font-weight:500}
.note{color:var(--muted);font-size:13px;margin:0 0 16px;max-width:70ch}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:6px;
padding:18px;margin-bottom:20px}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px;
font-family:ui-monospace,monospace}
input[type=text],input[type=password],input[type=number],select{width:100%;padding:8px 10px;
border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--ink);
font:13px ui-monospace,monospace}
input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--accent);
outline-offset:1px}
.field{margin-bottom:14px}
.grid2{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:720px){.grid2{grid-template-columns:1fr 1fr}}
button{font:500 14px system-ui;padding:9px 18px;border-radius:4px;cursor:pointer;
background:var(--accent);color:var(--surface);border:1px solid var(--accent)}
button:hover{opacity:.88}
button.sec{background:var(--surface);color:var(--ink);border-color:var(--line)}
button.sec:hover{border-color:var(--accent);color:var(--accent);opacity:1}
.searchbar{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.searchbar .field{flex:1 1 260px;margin:0}
.opts{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px;padding-top:14px;
border-top:1px solid var(--line);align-items:flex-end}
.opts .field{margin:0;min-width:110px}
.chk{display:flex;align-items:center;gap:7px;font-size:13px;cursor:pointer;padding-bottom:8px}
.chk input{width:15px;height:15px;accent-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.05em;
color:var(--muted);font-weight:500;padding:0 10px 8px 0;border-bottom:1px solid var(--line)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
td.num{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;text-align:right}
.tblwrap{overflow-x:auto}
.score{font-family:ui-monospace,monospace;font-weight:600;color:var(--accent)}
.tag{display:inline-block;font:11px ui-monospace,monospace;padding:2px 7px;border-radius:3px;
background:var(--surface2);color:var(--muted);white-space:nowrap}
/* Причина отсева бывает длинной — переносим её, а не обрезаем */
.tag.fail,.tag.black{white-space:normal;max-width:34ch;line-height:1.45}
td:last-child{min-width:150px}
.tag.pass{background:var(--passbg);color:var(--pass)}
.tag.fail{background:var(--failbg);color:var(--fail)}
.tag.black{background:var(--warnbg);color:var(--warn)}
.flash{padding:12px 14px;border-radius:4px;margin-bottom:18px;font-size:14px}
.flash.ok{background:var(--passbg);color:var(--pass)}
.flash.err{background:var(--failbg);color:var(--fail)}
.flash.warn{background:var(--warnbg);color:var(--ink)}
.src{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px solid var(--line)}
.src:last-child{border-bottom:none}
.src .dot{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none}
.src .dot.on{background:var(--pass)}
.src .dot.off{background:var(--muted);opacity:.5}
.src b{font-weight:600;font-size:14px}
.src p{margin:3px 0 0;font-size:13px;color:var(--muted)}
code{font:12px ui-monospace,monospace;background:var(--surface2);padding:2px 6px;border-radius:3px}
.tally{display:flex;gap:22px;flex-wrap:wrap;font:13px ui-monospace,monospace;
padding:12px 0;color:var(--muted)}
.tally b{color:var(--ink);font-size:15px}
footer{border-top:1px solid var(--line);margin-top:28px;padding-top:18px;color:var(--muted);
font-size:13px}
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _page(title: str, active: str, body: str, flash: Optional[tuple[str, str]] = None) -> str:
    flash_html = ""
    if flash:
        kind, text = flash
        flash_html = f'<div class="flash {_esc(kind)}">{_esc(text)}</div>'
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (
            ("search", "/", "Поиск"),
            ("settings", "/settings", "Ключи и источники"),
        )
    )
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body><div class=wrap>"
        f"<header><h1>Пульт хантинга</h1><nav>{nav}</nav></header>"
        f"{flash_html}{body}"
        "<footer>Интерфейс работает только на этой машине (127.0.0.1). "
        "Ключи лежат в <code>.env</code> с правами 600 и никуда не отправляются.</footer>"
        "</div></body></html>"
    )


def _source_options(state: AppState, selected: str) -> str:
    options = []
    for info in describe_sources(state.settings()):
        mark = "" if info.ready else " — не настроен"
        sel = " selected" if info.key == selected else ""
        options.append(
            f'<option value="{_esc(info.key)}"{sel}>{_esc(info.title)}{_esc(mark)}</option>'
        )
    return "".join(options)


def render_search(state: AppState, query: str = "", source: Optional[str] = None) -> str:
    source = source or state.default_source
    result = state.last_result

    body = [
        '<form method="post" action="/search"><div class="panel">',
        f'<input type="hidden" name="csrf" value="{_esc(state.csrf)}">',
        "<h2>Запрос</h2>",
        '<p class="note">Несколько запросов — через запятую. Источник выбирается справа: '
        "демо работает без всяких ключей.</p>",
        '<div class="searchbar">',
        '<div class="field"><label for="q">Что ищем</label>',
        f'<input type="text" id="q" name="q" value="{_esc(query)}" '
        'placeholder="трейдинг, фьючерсы, скальпинг" autofocus></div>',
        '<div class="field" style="flex:0 1 220px"><label for="source">Источник</label>'
        f'<select id="source" name="source">{_source_options(state, source)}</select></div>',
        "<button type=submit>Искать</button>",
        "</div>",
        '<div class="opts">',
        '<div class="field"><label for="limit">Каналов на запрос</label>'
        '<input type="number" id="limit" name="limit" value="30" min="1" max="100"></div>',
        '<div class="field"><label for="min_subs">Подписчиков от</label>'
        '<input type="number" id="min_subs" name="min_subs" value="1000" min="0"></div>',
        '<div class="field"><label for="max_subs">до</label>'
        '<input type="number" id="max_subs" name="max_subs" value="1000000" min="0"></div>',
        '<label class="chk"><input type="checkbox" name="expand" value="1">'
        "Дотянуть рекомендациями</label>",
        '<label class="chk"><input type="checkbox" name="loose" value="1">'
        "Без порогов ER и живости</label>",
        "</div></div></form>",
    ]

    if result:
        body.append(_render_results(state, result))

    return _page("Поиск каналов", "search", "".join(body), state.flash)


def _render_results(state: AppState, result: dict[str, Any]) -> str:
    rows = result.get("rows") or []
    head = [
        '<div class="panel"><h2>Выдача</h2>',
        f'<div class="tally"><span><b>{len(rows)}</b> найдено</span>'
        f'<span><b>{result.get("new", 0)}</b> новых</span>'
        f'<span><b>{result.get("known", 0)}</b> уже в базе</span>'
        f'<span>запрос: {_esc(result.get("query"))}</span>'
        f'<span>источник: {_esc(result.get("source"))}</span></div>',
    ]

    if not rows:
        head.append('<p class="note">Ничего не нашлось. Попробуйте другой запрос, '
                    "другой источник или снимите пороги.</p></div>")
        return "".join(head)

    head.append('<div class="tblwrap"><table><thead><tr>'
                "<th>score</th><th>канал</th><th>подписчики</th><th>ER %</th>"
                "<th>постов/30д</th><th>контакт</th><th>вердикт</th>"
                "</tr></thead><tbody>")

    for row in rows:
        if row.get("blacklist_hit"):
            tag = f'<span class="tag black">blacklist: {_esc(row.get("blacklist_markers"))}</span>'
        elif row.get("known"):
            tag = '<span class="tag">уже в базе</span>'
        elif row.get("passed_filters"):
            tag = '<span class="tag pass">в выгрузку</span>'
        else:
            tag = f'<span class="tag fail">{_esc(row.get("filter_reasons") or "отсев")}</span>'

        username = row.get("username") or str(row.get("channel_id"))
        head.append(
            f'<tr><td class="num score">{int(row.get("score") or 0)}</td>'
            f'<td><a href="https://t.me/{_esc(username)}" target="_blank" rel="noopener">'
            f'@{_esc(username)}</a><br><span class="tag">{_esc(row.get("title") or "")}</span></td>'
            f'<td class="num">{int(row.get("subscribers") or 0)}</td>'
            f'<td class="num">{float(row.get("er") or 0):.1f}</td>'
            f'<td class="num">{int(row.get("posts_last_30d") or 0)}</td>'
            f'<td>{_esc(row.get("contact") or "—")}</td>'
            f"<td>{tag}</td></tr>"
        )

    head.append("</tbody></table></div>")
    if state.last_csv:
        head.append(
            f'<p class="note" style="margin-top:14px">CSV: <code>{_esc(state.last_csv)}</code> '
            f'— <a href="/export">скачать</a></p>'
        )
    head.append("</div>")
    return "".join(head)


def render_settings(state: AppState) -> str:
    values = masked_values(state.env_file)
    settings = state.settings()

    sources = ['<div class="panel"><h2>Источники</h2>',
               '<p class="note">Что уже настроено и что каждый источник умеет.</p>']
    for info in describe_sources(settings):
        dot = "on" if info.ready else "off"
        sources.append(
            f'<div class="src"><span class="dot {dot}"></span><div>'
            f"<b>{_esc(info.title)}</b> <span class=tag>{_esc(info.methods)}</span>"
            f"<p>{_esc(info.hint)}</p></div></div>"
        )
    sources.append("</div>")

    fields = ['<form method="post" action="/settings"><div class="panel">',
              f'<input type="hidden" name="csrf" value="{_esc(state.csrf)}">',
              "<h2>Ключи сервисов</h2>",
              '<p class="note">Сохраняются в <code>.env</code> рядом с проектом, права 600. '
              "Секреты показываются маской — пустое поле означает «не менять».</p>",
              '<div class="grid2">']
    for key, title, hint in FIELDS:
        shown = values.get(key, "")
        is_secret = shown and "…" in shown
        placeholder = f"сейчас: {shown}" if is_secret else ""
        value = "" if is_secret else shown
        fields.append(
            f'<div class="field"><label for="{_esc(key)}">{_esc(title)} — {_esc(hint)}</label>'
            f'<input type="text" id="{_esc(key)}" name="{_esc(key)}" value="{_esc(value)}" '
            f'placeholder="{_esc(placeholder)}" autocomplete="off" spellcheck="false"></div>'
        )
    fields.append('</div><div style="margin-top:8px"><button type=submit>Сохранить</button>'
                  '</div></div></form>')

    session_note = [
        '<div class="panel"><h2>Вход в Telegram</h2>',
        '<p class="note">Для источника Telegram одних ключей мало: глобальный поиск требует '
        "юзер-сессии. Её создают один раз в терминале — нужен код из SMS, поэтому через "
        "браузер это не делается:</p>",
        "<p><code>python -m tghunter login</code></p>",
        '<p class="note">Берите отдельный аккаунт, не личный: файл сессии равносилен паролю '
        "от него. TGStat такого не требует — там достаточно ключа.</p></div>",
    ]

    return _page("Ключи и источники", "settings",
                 "".join(sources + fields + session_note), state.flash)


# --------------------------------------------------------------------------
# Сервер
# --------------------------------------------------------------------------

def _run_search(state: AppState, form: dict[str, list[str]]) -> tuple[str, str]:
    """Выполняет поиск. Возвращает (тип сообщения, текст) для плашки."""
    raw_query = (form.get("q") or [""])[0]
    queries = split_queries(raw_query)
    if not queries:
        return "warn", "Пустой запрос — нечего искать"

    source_key = (form.get("source") or [state.default_source])[0]
    settings = state.settings()

    def _int(name: str, default: int) -> int:
        try:
            return int((form.get(name) or [str(default)])[0])
        except ValueError:
            return default

    if source_key == "demo":
        settings.rate_min_interval = settings.rate_max_interval = 0.0
        settings.method_pause_min = settings.method_pause_max = 0.0

    limiter = RateLimiter(
        min_interval=settings.rate_min_interval,
        max_interval=settings.rate_max_interval,
        floodwait_abort_streak=settings.floodwait_abort_streak,
    )

    try:
        gateway = build_source(settings, limiter, source_key)
    except Exception as exc:
        return "err", f"Источник «{source_key}» не поднялся: {exc}"

    stream = build_adhoc_stream(
        queries,
        min_subs=_int("min_subs", 1000),
        max_subs=_int("max_subs", 1000000),
        languages=[],
        loose=bool(form.get("loose")),
    )

    db = Database(settings.db_path)
    try:
        result = run_search(
            gateway, db, settings, limiter, queries, stream,
            limit_per_query=_int("limit", 30),
            expand=bool(form.get("expand")),
            config_dir=settings.config_dir,
        )
        result["source"] = source_key
        state.last_result = result

        exportable = [
            r for r in result["rows"] if r["passed_filters"] and not r["blacklist_hit"]
        ]
        state.last_csv = None
        if exportable:
            slug = "".join(c if c.isalnum() else "_" for c in result["query"])[:40] or "search"
            path = export_dir_for_today(settings.export_dir) / f"search_{slug}.csv"
            export_csv(exportable, path)
            state.last_csv = path
    finally:
        gateway.disconnect()
        db.close()

    if result["aborted"]:
        return "err", f"Остановлено по лимитам Telegram: {result['note']}"
    return "ok", f"Найдено {result['found']}, из них новых {result['new']}"


class Handler(BaseHTTPRequestHandler):
    state: AppState  # проставляется при создании сервера

    server_version = "tghunter"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("web: " + fmt, *args)

    # --- вспомогательное --------------------------------------------------

    def _authorized(self) -> bool:
        """Доступ по токену: в первый раз — из ?t=, дальше — из cookie."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if (params.get("t") or [""])[0] == self.state.token:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        return bool(morsel and morsel.value == self.state.token)

    def _send(self, body: str, status: int = 200, set_cookie: bool = False) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self.state.token}; Path=/; HttpOnly; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _deny(self) -> None:
        self._send(
            _page("Нет доступа", "", '<div class="panel"><h2>Нет доступа</h2>'
                  '<p class="note">Откройте ссылку с токеном — она напечатана в терминале '
                  "при запуске.</p></div>"),
            status=403,
        )

    def _form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw, keep_blank_values=True)

    # --- маршруты ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — имя задано BaseHTTPRequestHandler
        if not self._authorized():
            return self._deny()
        path = urlparse(self.path).path
        fresh = "t=" in (urlparse(self.path).query or "")
        state = self.state
        flash, state.flash = state.flash, None

        if path == "/":
            state.flash = flash
            page = render_search(state)
            state.flash = None
            return self._send(page, set_cookie=fresh)
        if path == "/settings":
            state.flash = flash
            page = render_settings(state)
            state.flash = None
            return self._send(page, set_cookie=fresh)
        if path == "/export":
            return self._send_csv()
        if path == "/health":
            return self._send(json.dumps({"ok": True}))

        self._send(_page("Не найдено", "", '<div class="panel">Нет такой страницы</div>'), 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._deny()
        form = self._form()
        if (form.get("csrf") or [""])[0] != self.state.csrf:
            return self._deny()

        path = urlparse(self.path).path
        if path == "/search":
            if not _search_lock.acquire(blocking=False):
                self.state.flash = ("warn", "Поиск уже идёт — дождитесь окончания")
                return self._redirect("/")
            try:
                self.state.flash = _run_search(self.state, form)
            except Exception as exc:  # не роняем сервер на ошибке одного запроса
                log.exception("Поиск упал")
                self.state.flash = ("err", f"Поиск упал: {exc}")
            finally:
                _search_lock.release()
            return self._redirect("/")

        if path == "/settings":
            updates = {}
            for key, _title, _hint in FIELDS:
                value = (form.get(key) or [""])[0].strip()
                if value:  # пустое поле означает «не менять»
                    updates[key] = value
            if updates:
                changed = write_env(updates, self.state.env_file)
                apply_to_environ(updates)
                self.state.flash = (
                    "ok", f"Сохранено в .env: {', '.join(changed) if changed else 'без изменений'}"
                )
            else:
                self.state.flash = ("warn", "Ничего не ввели — нечего сохранять")
            return self._redirect("/settings")

        self._send(_page("Не найдено", "", '<div class="panel">Нет такой страницы</div>'), 404)

    def _send_csv(self) -> None:
        path = self.state.last_csv
        if not path or not Path(path).exists():
            return self._send(
                _page("Нет файла", "search",
                      '<div class="panel">Сначала выполните поиск.</div>'), 404
            )
        data = Path(path).read_bytes()
        name = Path(path).name
        # Заголовки HTTP обязаны быть latin-1, а имя файла содержит кириллицу
        # (search_трейдинг.csv) — отдаём ASCII-запасной вариант плюс RFC 5987.
        ascii_name = name.encode("ascii", "ignore").decode("ascii") or "export.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(name, safe='')}",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def build_server(state: AppState, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    return ThreadingHTTPServer((HOST, port), handler)


def serve(
    port: int = DEFAULT_PORT,
    env_file: str = ".env",
    db_path: Optional[str] = None,
    default_source: str = "demo",
    open_browser: bool = True,
) -> None:
    state = AppState(env_file=env_file, db_path=db_path, default_source=default_source)
    server = build_server(state, port)
    url = f"http://{HOST}:{port}/?t={state.token}"

    print("Локальный интерфейс запущен. Откройте ссылку (она содержит токен доступа):")
    print(f"\n    {url}\n")
    print("Ctrl+C — остановить. Наружу сервер не смотрит: только 127.0.0.1.")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
