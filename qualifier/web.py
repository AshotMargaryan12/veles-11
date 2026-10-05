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
from .render import card_to_dict, render_markdown
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


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Квалификатор партнёра</title>
<style>
:root{--bg:#f1f4f4;--surface:#fff;--ink:#131a1b;--muted:#5a6769;--line:#d5dede;
--accent:#0e6e68;--warn:#8a5d06;--warnbg:#f6ead2;--fail:#b23a25;--failbg:#f6e0db;}
@media(prefers-color-scheme:dark){:root{--bg:#0d1213;--surface:#151d1e;--ink:#e4ebea;
--muted:#8b9b9b;--line:#273435;--accent:#3fc7bc;--warn:#d9a43c;--warnbg:#2a2113;
--fail:#e3705a;--failbg:#2e1a16;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif;padding:0 16px 56px}
.wrap{max-width:1000px;margin:0 auto}
h1{font-size:20px;margin:24px 0 4px}
p.note{color:var(--muted);margin:0 0 16px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
label{display:block;font-weight:600;margin:10px 0 4px}
textarea,input,select{width:100%;font:inherit;color:var(--ink);background:var(--bg);
border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.row{grid-template-columns:1fr}}
.checks{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px}
.checks label{font-weight:400;display:flex;gap:6px;align-items:center;margin:0}
.checks input{width:auto}
button{font:inherit;font-weight:600;border:0;border-radius:8px;padding:10px 18px;
background:var(--accent);color:var(--surface);cursor:pointer;margin-top:14px}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent);margin-left:8px}
button:disabled{opacity:.6;cursor:wait}
pre{white-space:pre-wrap;word-break:break-word;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
.error{background:var(--failbg);color:var(--fail);border-radius:8px;padding:10px 12px;margin-top:12px}
.hidden{display:none}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600}
a{color:var(--accent)}
.overflow{overflow-x:auto}
</style></head><body><div class="wrap">
<h1>Квалификатор партнёра</h1>
<p class="note">Ссылки на площадки блогера → карточка: метрики, гео, тир, CPA, флаги и черновик заявки.
Оценка предварительная, решение — за менеджером.</p>
<div class="panel">
<label for="links">Ссылки партнёра (через пробел или с новой строки)</label>
<textarea id="links" rows="3" placeholder="t.me/channel  youtube.com/@channel  x.com/handle"></textarea>
<div class="row">
<div><label for="geo">Гео (если знаете точно)</label><input id="geo" maxlength="2" placeholder="EG"></div>
<div><label for="type">Тип партнёра</label><select id="type">
<option value="individual">individual</option><option value="institutional">institutional</option></select></div>
</div>
<label for="notes">Заметки менеджера (попадут в черновик)</label>
<textarea id="notes" rows="2"></textarea>
<div class="checks">
<label><input type="checkbox" id="refresh"> обновить данные (не из кэша)</label>
<label><input type="checkbox" id="nollm"> без LLM</label>
</div>
<button id="go">Квалифицировать</button>
<div id="err" class="error hidden"></div>
</div>
<div id="result" class="panel hidden">
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
<strong>Карточка</strong>
<span><button id="copyDraft" class="secondary">Скопировать черновик</button>
<button id="saveJson" class="secondary">Скачать JSON</button></span>
</div>
<pre id="card" style="margin-top:12px"></pre>
</div>
<div class="panel"><strong>Проверено за 30 дней</strong>
<div class="overflow"><table id="history"><thead><tr><th>Когда</th><th>Партнёр</th><th>Тир</th>
<th>ER</th><th>Гео</th><th>Флаги</th></tr></thead><tbody></tbody></table></div></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
let last = null;
function esc(s){return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
async function loadHistory(){
  const r = await fetch("/api/history?days=30");
  if(!r.ok) return;
  const rows = await r.json();
  $("history").querySelector("tbody").innerHTML = rows.map(x => `<tr><td>${esc(x.created_at.slice(0,16).replace("T"," "))}</td>
  <td>${esc(x.partner)}</td><td>${x.tier==null?"—":esc(x.tier)+"%"}</td><td>${x.er==null?"—":esc(x.er)+"%"}</td>
  <td>${esc(x.geo||"?")} (${esc(x.geo_confidence)})</td><td>${esc(x.flags||"—")}</td></tr>`).join("") ||
  '<tr><td colspan="6">пока пусто</td></tr>';
}
$("go").onclick = async () => {
  $("err").classList.add("hidden"); $("go").disabled = true; $("go").textContent = "Собираем данные…";
  try {
    const r = await fetch("/api/qualify", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({links:$("links").value, geo:$("geo").value, type:$("type").value,
        notes:$("notes").value, refresh:$("refresh").checked, no_llm:$("nollm").checked})});
    const data = await r.json();
    if(!r.ok) throw new Error(data.error || data.detail || r.statusText);
    last = data; $("card").textContent = data.markdown; $("result").classList.remove("hidden");
    loadHistory();
  } catch(e) { $("err").textContent = e.message; $("err").classList.remove("hidden"); }
  finally { $("go").disabled = false; $("go").textContent = "Квалифицировать"; }
};
$("copyDraft").onclick = async () => { if(last){ await navigator.clipboard.writeText(last.draft);
  $("copyDraft").textContent = "Скопировано"; setTimeout(()=>$("copyDraft").textContent="Скопировать черновик",1500);} };
$("saveJson").onclick = () => { if(!last) return;
  const blob = new Blob([JSON.stringify(last.card, null, 2)], {type:"application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = (last.card.partner.handle||"partner").replace(/[^\\w-]/g,"") + ".json"; a.click(); };
loadHistory();
</script></body></html>
"""
