"""Локальный веб-интерфейс: одна страница — ссылки партнёра -> карточка.

FastAPI + uvicorn, только 127.0.0.1. Доступ по токену из ссылки, которую
печатает `qualify web`; дальше токен живёт в cookie. Все проверки выполняются
в одном рабочем потоке со своим event loop: Telethon не любит чужие потоки,
а Telegram-сессия переиспользуется между запросами.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import ConfigError
from .links import LinkError
from .models import PartnerRequest
from .render import card_summary, card_to_dict, render_markdown
from .service import BudgetExceeded, Qualifier

HOST = "127.0.0.1"
COOKIE = "qualifier_token"


def _init_loop() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())


class WebState:
    def __init__(self, factory: Any, token: Optional[str] = None):
        self.factory = factory           # () -> Qualifier, вызывается в рабочем потоке
        self.token = token or secrets.token_urlsafe(24)
        self.executor = ThreadPoolExecutor(max_workers=1, initializer=_init_loop)
        self._local = threading.local()

    def qualifier(self) -> Qualifier:
        if getattr(self._local, "q", None) is None:
            self._local.q = self.factory()
        return self._local.q


def _run(state: WebState, payload: dict[str, Any]) -> dict[str, Any]:
    links = [l for l in str(payload.get("links") or "").replace(",", " ").split() if l]
    if not links:
        raise LinkError("вставьте хотя бы одну ссылку")
    geo = (payload.get("geo") or "").strip().upper() or None
    if geo and len(geo) != 2:
        raise LinkError("гео — двухбуквенный код страны, например EG")
    q = state.qualifier()
    q.fetches = 0  # лимит MAX_CHANNELS_PER_RUN — на один запрос страницы
    request = PartnerRequest(
        links=links,
        geo=geo,
        affiliate_type=payload.get("type") or "individual",
        notes=str(payload.get("notes") or ""),
        refresh=bool(payload.get("refresh")),
    )
    llm_backup = q.llm
    if payload.get("no_llm"):
        q.llm = None
    try:
        card = q.run(request)
    finally:
        q.llm = llm_backup
    q.save(card)
    template = None
    try:
        template = q.config.template("card.md")
    except ConfigError:
        pass
    return {
        "markdown": render_markdown(card, template),
        "draft": card.draft,
        "summary": card_summary(card),
        "card": card_to_dict(card),
    }


def _history(state: WebState, days: int) -> list[dict[str, Any]]:
    q = state.qualifier()
    return q.store.history(days) if q.store is not None else []


def create_app(state: WebState) -> FastAPI:
    app = FastAPI(title="Квалификатор партнёра", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        token = request.query_params.get("token") or request.cookies.get(COOKIE) \
            or request.headers.get("x-token")
        return bool(token) and secrets.compare_digest(token, state.token)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        if not authorized(request):
            return HTMLResponse(
                "<p>Нет доступа. Откройте ссылку с токеном, которую напечатала команда "
                "<code>qualify web</code>.</p>", status_code=403,
            )
        response = HTMLResponse(PAGE)
        response.set_cookie(COOKIE, state.token, httponly=True, samesite="strict")
        return response

    @app.post("/api/qualify")
    async def qualify_endpoint(request: Request) -> JSONResponse:
        if not authorized(request):
            raise HTTPException(403, "нет доступа")
        payload = await request.json()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(state.executor, _run, state, payload)
        except (LinkError, ConfigError, BudgetExceeded) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # показываем человеку, а не 500 без текста
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
        return JSONResponse(result)

    @app.get("/api/history")
    async def history_endpoint(request: Request, days: int = 30) -> JSONResponse:
        if not authorized(request):
            raise HTTPException(403, "нет доступа")
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(state.executor, _history, state, days)
        return JSONResponse(rows)

    return app


def serve(args: argparse.Namespace, port: int = 8766, open_browser: bool = True) -> int:
    import uvicorn

    from .cli import _settings, build_qualifier

    def factory() -> Qualifier:
        return build_qualifier(args, _settings(args))

    state = WebState(factory)
    url = f"http://{HOST}:{port}/?token={state.token}"
    print(f"Квалификатор: {url}")
    print("Сервер слушает только 127.0.0.1. Остановить: Ctrl+C")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(state), host=HOST, port=port, log_level="warning")
    return 0


# Страница в палитре Binance: тёмная тема по умолчанию, светлая — переключателем.
# Контраст текста проверен (WCAG 4.5:1): подписи всегда в цветах текста, статусный
# цвет несут иконка и рамка; жёлтый в светлой теме — только заливкой.
# Всё, что приходит из каналов (названия, цитаты), вставляется через textContent.
PAGE = """<!doctype html>
<html lang="ru" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Квалификатор партнёра</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<script>try{var t=localStorage.getItem("qualifier-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}</script>
<style>
:root{
  color-scheme:dark;
  --bg:#0B0E11;--surface:#181A20;--surface2:#1E2329;--line:#2B3139;--line2:#474D57;
  --ink:#EAECEF;--ink2:#B7BDC6;--muted:#848E9C;
  --accent:#FCD535;--accent-hover:#F0B90B;--on-accent:#181A20;--accent-text:#F0B90B;
  --good:#0ECB81;--warn:#F0B90B;--bad:#F6465D;
  --good-bg:#1C3B35;--warn-bg:#3B3825;--bad-bg:#3C2830;
  --font:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#F5F5F5;--surface:#FFFFFF;--surface2:#F5F5F5;--line:#EAECEF;--line2:#C5CBD3;
  --ink:#1E2329;--ink2:#474D57;--muted:#5E6673;
  --accent:#FCD535;--accent-hover:#F0B90B;--on-accent:#1E2329;--accent-text:#8A6500;
  --good:#027A50;--warn:#B08300;--bad:#CF304A;
  --good-bg:#DDF8ED;--warn-bg:#FDF5DD;--bad-bg:#FEE5E8;
}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--ink);font:15px/1.5 var(--font);padding:0 0 56px}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}
.top{background:var(--surface);border-bottom:1px solid var(--line)}
.bar{display:flex;align-items:center;justify-content:space-between;gap:12px;height:60px}
.logo{display:flex;align-items:center;gap:10px;font-weight:600;font-size:17px}
.mark{width:22px;height:22px;border-radius:6px;background:var(--accent)}
.sub{color:var(--muted);margin:20px 0 4px;font-size:14px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
h2{font-size:20px;font-weight:600;margin:0}
h3{font-size:15px;font-weight:600;margin:0}
label{display:block;color:var(--ink2);font-size:13px;font-weight:500;margin:14px 0 6px}
textarea,input,select{width:100%;font:inherit;color:var(--ink);background:var(--surface2);
  border:1px solid var(--line);border-radius:8px;padding:10px 12px;outline:none}
textarea:focus,input:focus,select:focus{border-color:var(--accent-hover)}
::placeholder{color:var(--muted)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.checks{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px}
.checks label{display:flex;gap:8px;align-items:center;margin:0;color:var(--ink2);font-weight:400;font-size:14px}
.checks input{width:16px;height:16px;accent-color:var(--accent-hover)}
button{font:inherit;font-weight:600;border-radius:8px;cursor:pointer;padding:10px 20px;border:1px solid transparent}
.primary{background:var(--accent);color:var(--on-accent);margin-top:18px;min-width:200px}
.primary:hover{background:var(--accent-hover)}
.primary:disabled{opacity:.6;cursor:wait}
.ghost{background:transparent;color:var(--ink);border-color:var(--line2);padding:8px 14px;font-size:14px}
.ghost:hover{border-color:var(--accent-hover);color:var(--accent-text)}
.error{display:flex;gap:10px;background:var(--bad-bg);border:1px solid var(--bad);color:var(--ink);
  border-radius:8px;padding:10px 12px;margin-top:14px}
.hidden{display:none!important}
.muted{color:var(--muted);font-size:14px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.verdict{display:flex;gap:12px;border-radius:10px;padding:14px 16px;margin-top:16px;
  background:var(--surface2);border:1px solid var(--line);border-left:4px solid var(--line2)}
.verdict p{margin:0 0 4px}.verdict p:last-child{margin:0}
.verdict p+p{color:var(--ink2);font-size:14px}
.verdict.good{background:var(--good-bg);border-color:var(--good)}
.verdict.warn{background:var(--warn-bg);border-color:var(--warn)}
.verdict.critical{background:var(--bad-bg);border-color:var(--bad)}
.ico{flex:none;display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;
  font-size:12px;font-weight:700;line-height:1}
.good .ico,.ico.good{background:var(--good);color:var(--surface)}
.warn .ico,.ico.warn{background:var(--warn);color:var(--surface)}
.critical .ico,.ico.critical{background:var(--bad);color:var(--surface)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}
.tile .label{color:var(--muted);font-size:13px}
.tile .value{font-size:26px;font-weight:600;line-height:1.25;margin:4px 0 2px;overflow-wrap:anywhere}
.tile .note{color:var(--ink2);font-size:13px;display:flex;gap:6px;align-items:center}
.tile .note .ico{width:16px;height:16px;font-size:10px}
/* одна сетка на весь список: чипы разной ширины не сбивают колонку текста */
.flags{list-style:none;margin:16px 0 0;padding:0;display:grid;grid-template-columns:max-content 1fr;gap:6px 12px;align-items:start}
.flags li{display:contents}
.flags .chip{margin-top:6px;justify-self:start}
.flags .text{padding-top:6px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);border-radius:999px;
  padding:3px 10px 3px 4px;font-size:13px;font-weight:500;color:var(--ink);white-space:nowrap;font-family:var(--mono)}
.chip.warn{background:var(--warn-bg);border-color:var(--warn)}
.chip.critical{background:var(--bad-bg);border-color:var(--bad)}
.chip.good{background:var(--good-bg);border-color:var(--good)}
.flags .text{color:var(--ink2);font-size:14px}
.flags .ev{grid-column:2;color:var(--muted);font-size:13px;overflow-wrap:anywhere;margin-top:-4px}
@media(max-width:640px){.flags{grid-template-columns:1fr}.flags .ev{grid-column:1}}
.reasons{margin:8px 0 0;padding-left:20px;color:var(--ink2);font-size:14px}
.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 var(--mono);margin:0;color:var(--ink)}
details summary{cursor:pointer;font-weight:600;list-style:none;display:flex;justify-content:space-between}
details summary::after{content:"Показать";color:var(--accent-text);font-weight:500;font-size:14px}
details[open] summary::after{content:"Скрыть"}
details[open] summary{margin-bottom:12px}
.warnings{margin:12px 0 0;padding:0;list-style:none;color:var(--muted);font-size:13px}
.warnings li::before{content:"⚠ ";color:var(--warn)}
.overflow{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:13px}
td.num{font-variant-numeric:tabular-nums}
tr:hover td{background:var(--surface2)}
@media(max-width:640px){.row{grid-template-columns:1fr}.primary{width:100%}
  .tiles{grid-template-columns:1fr 1fr}.tile .value{font-size:22px}}
</style>
</head>
<body>
<header class="top"><div class="wrap bar">
  <span class="logo"><span class="mark" aria-hidden="true"></span>Квалификатор партнёра</span>
  <button id="theme" class="ghost" type="button">Светлая тема</button>
</div></header>
<main class="wrap">
<p class="sub">Ссылки на площадки блогера → карточка: метрики, гео, тир, CPA, флаги и черновик заявки.
Оценка предварительная, решение — за менеджером.</p>

<section class="panel">
  <label for="links">Ссылки партнёра — через пробел или с новой строки</label>
  <textarea id="links" rows="3" placeholder="t.me/channel  youtube.com/@channel  x.com/handle"></textarea>
  <div class="row">
    <div><label for="geo">Гео, если знаете точно</label><input id="geo" maxlength="2" placeholder="EG"></div>
    <div><label for="type">Тип партнёра</label><select id="type">
      <option value="individual">individual</option><option value="institutional">institutional</option></select></div>
  </div>
  <label for="notes">Заметки менеджера — попадут в черновик</label>
  <textarea id="notes" rows="2"></textarea>
  <div class="checks">
    <label><input type="checkbox" id="refresh"> обновить данные, не из кэша</label>
    <label><input type="checkbox" id="nollm"> без LLM</label>
  </div>
  <button id="go" class="primary" type="button">Квалифицировать</button>
  <div id="err" class="error hidden" role="alert"><span class="ico critical">!</span><span id="errText"></span></div>
</section>

<section id="result" class="hidden">
  <div class="panel">
    <div class="head">
      <div><h2 id="pName"></h2><div id="pPlatforms" class="muted"></div></div>
      <button id="saveJson" class="ghost" type="button">Скачать JSON</button>
    </div>
    <div id="verdict" class="verdict"></div>
    <div id="tiles" class="tiles"></div>
    <ul id="flags" class="flags"></ul>
    <ul id="warnings" class="warnings"></ul>
  </div>
  <div class="panel">
    <div class="section-title"><h3>Черновик заявки</h3>
      <button id="copyDraft" class="ghost" type="button">Скопировать черновик</button></div>
    <pre id="draft"></pre>
  </div>
  <details class="panel"><summary>Полная карточка</summary><pre id="card"></pre></details>
</section>

<section class="panel">
  <h3>Проверено за 30 дней</h3>
  <div class="overflow"><table id="history"><thead><tr><th>Когда</th><th>Партнёр</th><th>Тир</th>
  <th>ER</th><th>Гео</th><th>Флаги</th></tr></thead><tbody></tbody></table></div>
</section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const ICON = {good: "✓", warn: "!", critical: "!!"};
let last = null;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}
function icon(status) { return el("span", "ico " + status, ICON[status] || "!"); }

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "dark" ? "Светлая тема" : "Тёмная тема";
  try { localStorage.setItem("qualifier-theme", theme); } catch (e) {}
}
$("theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
setTheme(document.documentElement.dataset.theme || "dark");

function renderSummary(s) {
  $("pName").textContent = s.handle && s.handle !== s.partner ? `${s.partner} (${s.handle})` : s.partner;
  $("pPlatforms").textContent = s.platforms;

  const verdict = $("verdict");
  verdict.className = "verdict " + s.verdict;
  verdict.replaceChildren(icon(s.verdict));
  const body = el("div");
  (s.recommendation.length ? s.recommendation : ["Рекомендации нет"]).forEach(line => body.append(el("p", null, line)));
  verdict.append(body);

  const tiles = $("tiles");
  tiles.replaceChildren();
  s.tiles.forEach(t => {
    const tile = el("div", "tile");
    tile.append(el("div", "label", t.label), el("div", "value", t.value));
    const note = el("div", "note");
    if (t.status) note.append(icon(t.status));
    note.append(el("span", null, t.note || ""));
    tile.append(note);
    tiles.append(tile);
  });

  const flags = $("flags");
  flags.replaceChildren();
  const add = (status, code, text, evidence) => {
    const li = el("li");
    const chip = el("span", "chip " + status);
    chip.append(icon(status), el("span", null, code));
    li.append(chip, el("span", "text", text));
    if (evidence) li.append(el("span", "ev", evidence));
    flags.append(li);
  };
  s.flags.forEach(f => add(f.severity === "critical" ? "critical" : "warn", f.code, f.explanation, f.evidence));
  if (s.manual_review) add("warn", "ручная проверка", s.manual_review_reasons.join("; "));
  if (!s.flags.length && !s.manual_review) add("good", "флагов нет", "автоматических флагов риска не найдено");

  const warnings = $("warnings");
  warnings.replaceChildren(...s.warnings.map(w => el("li", null, w)));
}

async function loadHistory() {
  const r = await fetch("/api/history?days=30");
  if (!r.ok) return;
  const rows = await r.json();
  const tbody = $("history").querySelector("tbody");
  tbody.replaceChildren();
  if (!rows.length) { const tr = el("tr"); const td = el("td", "muted", "пока пусто"); td.colSpan = 6; tr.append(td); tbody.append(tr); return; }
  rows.forEach(x => {
    const tr = el("tr");
    tr.append(
      el("td", "num", x.created_at.slice(0, 16).replace("T", " ")),
      el("td", null, x.partner),
      el("td", "num", x.tier == null ? "—" : x.tier + "%"),
      el("td", "num", x.er == null ? "—" : x.er + "%"),
      el("td", null, `${x.geo || "?"} (${x.geo_confidence})`),
      el("td", null, x.flags || "—"),
    );
    tbody.append(tr);
  });
}

$("go").onclick = async () => {
  $("err").classList.add("hidden");
  $("go").disabled = true; $("go").textContent = "Собираем данные…";
  try {
    const r = await fetch("/api/qualify", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({links: $("links").value, geo: $("geo").value, type: $("type").value,
        notes: $("notes").value, refresh: $("refresh").checked, no_llm: $("nollm").checked})});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
    last = data;
    renderSummary(data.summary);
    $("draft").textContent = data.draft;
    $("card").textContent = data.markdown;
    $("result").classList.remove("hidden");
    loadHistory();
  } catch (e) {
    $("errText").textContent = e.message; $("err").classList.remove("hidden");
  } finally {
    $("go").disabled = false; $("go").textContent = "Квалифицировать";
  }
};
$("copyDraft").onclick = async () => {
  if (!last) return;
  try { await navigator.clipboard.writeText(last.draft); $("copyDraft").textContent = "Скопировано"; }
  catch (e) { $("copyDraft").textContent = "Не удалось скопировать"; }
  setTimeout(() => $("copyDraft").textContent = "Скопировать черновик", 1500);
};
$("saveJson").onclick = () => {
  if (!last) return;
  const blob = new Blob([JSON.stringify(last.card, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (last.card.partner.handle || "partner").replace(/[^A-Za-z0-9_-]/g, "") + ".json";
  a.click();
};
loadHistory();
</script>
</body>
</html>
"""
