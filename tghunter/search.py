"""Свободный поиск по Telegram: ввёл запрос — получил ранжированный список.

В отличие от прогона пресета (`run --stream`), запрос задаётся руками и нигде
не сохраняется. Канал, уже известный базе, не перепроверяется по API, но в
выдаче показывается с пометкой — чтобы было видно, что он в работе.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .config import Settings, Stream, load_blacklist
from .db import Database, normalize_username
from .discovery import keyword_candidates, similar_candidates
from .pipeline import Pipeline
from .ratelimit import ChannelBudgetExceeded, RateLimiter, RunAborted, RunBudget

log = logging.getLogger(__name__)

# Сколько найденных каналов берём как эталонные для расширения через similar
EXPAND_SEEDS = 10


def split_queries(raw: str) -> list[str]:
    """`"трейдинг, скальпинг"` -> ['трейдинг', 'скальпинг']."""
    return [q.strip() for q in (raw or "").split(",") if q.strip()]


def build_adhoc_stream(
    queries: list[str],
    min_subs: int = 1000,
    max_subs: int = 1000000,
    languages: Optional[list[str]] = None,
    loose: bool = False,
    name: Optional[str] = None,
) -> Stream:
    """Собирает одноразовый пресет под запрос пользователя.

    Пустой список языков означает «любой язык» — фильтр по языку не применяется.
    `loose` включает режим longlist: пороги ER и живости не отсекают канал,
    но стоп-маркеры и границы по подписчикам продолжают работать.
    """
    return Stream(
        name=name or "search:" + ",".join(queries)[:60],
        keywords=list(queries),
        methods=["keywords"],
        min_subscribers=min_subs,
        max_subscribers=max_subs,
        languages=list(languages or []),
        mode="longlist" if loose else "normal",
    )


def run_search(
    gateway: Any,
    db: Database,
    settings: Settings,
    limiter: RateLimiter,
    queries: list[str],
    stream: Stream,
    limit_per_query: int = 30,
    expand: bool = False,
    max_channels: Optional[int] = None,
    config_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Ищет каналы по запросу, собирает метрики и возвращает ранжированную выдачу."""
    pipeline = Pipeline(gateway, db, settings, limiter, config_dir or settings.config_dir)
    blacklist = load_blacklist(config_dir or settings.config_dir, stream.extra_blacklist)
    budget = RunBudget(max_channels=max_channels or settings.max_channels_per_run)
    run_id = db.start_run(stream.name)

    status = "ok"
    note = ""
    touched: list[int] = []
    known_ids: list[int] = []
    checked = new_count = 0

    try:
        candidates = keyword_candidates(gateway, queries, limit_per_query)
        log.info("Поиск '%s': %d каналов из выдачи", ", ".join(queries), len(candidates))

        if expand and candidates:
            # расширяем выдачу нативными рекомендациями к самым релевантным находкам
            seeds = [c.username for c in candidates[:EXPAND_SEEDS] if c.username]
            if seeds:
                limiter.pause_between_methods(
                    settings.method_pause_min, settings.method_pause_max
                )
                extra = similar_candidates(gateway, seeds)
                log.info("Расширение через similar: +%d каналов", len(extra))
                seen = {c.key() for c in candidates}
                candidates += [c for c in extra if c.key() not in seen]

        for candidate in candidates:
            existing = db.find(
                channel_id=candidate.channel_id,
                username=normalize_username(candidate.username or ""),
            )
            if existing is not None:
                # уже в базе — показываем из хранилища, не тратя запрос к API
                known_ids.append(int(existing["channel_id"]))
                continue

            try:
                budget.check()
            except ChannelBudgetExceeded as exc:
                status = "budget_exhausted"
                note = str(exc)
                break

            result = pipeline.process_candidate(candidate, stream, blacklist, run_id)
            checked += 1
            if result is None:
                continue
            channel_id, is_new, _metrics = result
            touched.append(channel_id)
            if is_new:
                new_count += 1
                budget.consume()

    except RunAborted as exc:
        status = "aborted_by_limits"
        note = str(exc)
        log.error("Поиск остановлен: %s", exc)

    db.finish_run(run_id, status, checked, new_count, note)

    rows = db.rows_by_ids(touched + known_ids)
    known = set(known_ids)
    for row in rows:
        row["known"] = row["channel_id"] in known

    return {
        "query": ", ".join(queries),
        "stream": stream.name,
        "status": status,
        "note": note,
        "found": len(rows),
        "checked": checked,
        "new": new_count,
        "known": len(known_ids),
        "rows": rows,
        "aborted": status == "aborted_by_limits",
    }


def format_table(rows: list[dict[str, Any]], limit: int = 40) -> str:
    """Ранжированная выдача для терминала."""
    if not rows:
        return "Ничего не найдено."

    header = ("score", "канал", "подпис.", "ER%", "коммент", "контакт", "вердикт")
    lines = [
        f"{header[0]:>5}  {header[1]:<26} {header[2]:>8} {header[3]:>6} "
        f"{header[4]:>7}  {header[5]:<20} {header[6]}"
    ]
    lines.append("-" * 104)

    for row in rows[:limit]:
        if row["blacklist_hit"]:
            verdict = "blacklist: " + (row["blacklist_markers"] or "")[:28]
        elif row["known"]:
            verdict = "уже в базе"
        elif row["passed_filters"]:
            verdict = "в выгрузку"
        else:
            verdict = "отсев: " + (row["filter_reasons"] or "")[:34]

        username = "@" + (row["username"] or str(row["channel_id"]))
        lines.append(
            f"{row['score']:>5}  {username[:26]:<26} {row['subscribers']:>8} "
            f"{row['er']:>6.1f} {('да' if row['comments_enabled'] else '—'):>7}  "
            f"{(row['contact'] or '—')[:20]:<20} {verdict}"
        )

    if len(rows) > limit:
        lines.append(f"... и ещё {len(rows) - limit}. Полный список — в CSV.")
    return "\n".join(lines)
