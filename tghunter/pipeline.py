"""Конвейер прогона: поиск -> метрики -> скоринг -> фильтры -> база.

Соблюдает лимиты Telegram: троттлинг запросов, MAX_CHANNELS_PER_RUN,
паузы между методами, аварийная остановка на серии FloodWait.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Settings, Stream, load_blacklist, read_list_file
from .db import Database, normalize_username
from .discovery import (
    folder_candidates,
    keyword_candidates,
    mention_candidates,
    similar_candidates,
)
from .filters import check_filters
from .metrics import collect_metrics
from .models import (
    METHOD_FOLDERS,
    METHOD_KEYWORDS,
    METHOD_MENTIONS,
    METHOD_SIMILAR,
    Candidate,
)
from .ratelimit import ChannelBudgetExceeded, RateLimiter, RunAborted, RunBudget
from .scoring import format_breakdown, score_channel

log = logging.getLogger(__name__)

# Сколько последних постов читаем для метрик (хватает на ER, частоту и монетизацию)
POSTS_FOR_METRICS = 30

RUN_STATUS_OK = "ok"
RUN_STATUS_LIMITED = "aborted_by_limits"
RUN_STATUS_BUDGET = "budget_exhausted"


class Pipeline:
    """Прогон одного пресета."""

    def __init__(
        self,
        gateway: Any,
        db: Database,
        settings: Settings,
        limiter: RateLimiter,
        config_dir: Optional[str] = None,
    ):
        self.gateway = gateway
        self.db = db
        self.settings = settings
        self.limiter = limiter
        self.config_dir = Path(config_dir or settings.config_dir)

    # --- сбор кандидатов --------------------------------------------------

    def collect_candidates(self, stream: Stream, budget: RunBudget) -> list[Candidate]:
        """Запускает включённые методы поиска и склеивает кандидатов."""
        collected: dict[str, Candidate] = {}
        methods = [m for m in stream.methods if m]
        first = True

        for method in methods:
            if budget.exhausted:
                log.info("Лимит каналов исчерпан — остальные методы пропускаем")
                break

            if not first:
                # пауза 30-60 сек между методами поиска (раздел 9.4 ТЗ)
                self.limiter.pause_between_methods(
                    self.settings.method_pause_min, self.settings.method_pause_max
                )
            first = False

            try:
                found = self._run_method(method, stream)
            except RunAborted:
                raise
            except Exception as exc:
                log.warning("Метод %s упал: %s", method, exc)
                continue

            log.info("Метод %s: %d кандидатов", method, len(found))
            for candidate in found:
                # первый метод, нашедший канал, остаётся источником;
                # similar имеет приоритет — он даёт +25 к score
                key = candidate.key()
                existing = collected.get(key)
                if existing is None:
                    collected[key] = candidate
                elif (
                    candidate.method == METHOD_SIMILAR
                    and existing.method != METHOD_SIMILAR
                ):
                    collected[key] = candidate

        return list(collected.values())

    def _run_method(self, method: str, stream: Stream) -> list[Candidate]:
        if method == METHOD_KEYWORDS:
            if not stream.keywords:
                return []
            return keyword_candidates(
                self.gateway, stream.keywords, stream.keyword_results_per_query
            )

        if method == METHOD_SIMILAR:
            seeds = self._seeds(stream)
            if not seeds:
                log.warning(
                    "Метод similar: пустой файл эталонных каналов %s", stream.seed_file
                )
                return []
            return similar_candidates(self.gateway, seeds)

        if method == METHOD_MENTIONS:
            # граф строим от эталонных каналов и уже найденных каналов стрима
            seeds = self._seeds(stream) + self.db.seed_channels_for_mentions(stream.name)
            if not seeds:
                return []
            return mention_candidates(
                self.gateway,
                seeds,
                posts_per_channel=stream.mention_posts_per_channel,
                depth=stream.mention_depth,
                max_seeds=50,
            )

        if method == METHOD_FOLDERS:
            urls = read_list_file(self.config_dir / stream.addlist_file)
            if not urls:
                return []
            return folder_candidates(self.gateway, urls)

        log.warning("Неизвестный метод поиска: %s", method)
        return []

    def _seeds(self, stream: Stream) -> list[str]:
        return read_list_file(self.config_dir / stream.seed_file)

    # --- обработка --------------------------------------------------------

    def process_candidate(
        self,
        candidate: Candidate,
        stream: Stream,
        blacklist: list[str],
        run_id: Optional[int] = None,
    ) -> Optional[tuple[int, bool, dict[str, Any]]]:
        """Собирает метрики канала, скорит и пишет в базу."""
        username = normalize_username(candidate.username or "")
        if not username:
            # канал без публичного username обработать нельзя (read-only, без вступления)
            return None

        snapshot = self.gateway.fetch_snapshot(
            username,
            posts_limit=POSTS_FOR_METRICS,
            method=candidate.method,
            source_channel=candidate.source_channel,
        )
        if snapshot is None:
            log.debug("Не удалось получить данные канала @%s", username)
            return None

        metrics = collect_metrics(
            snapshot, blacklist_markers=blacklist, stream=stream.name
        )
        score, breakdown = score_channel(metrics)
        passed, reasons = check_filters(metrics, stream)

        channel_id, is_new = self.db.upsert_channel(
            metrics, score, format_breakdown(breakdown), stream.name, passed, reasons
        )
        self.db.log_candidate(run_id, candidate.key(), candidate.method, candidate.source_channel)
        return channel_id, is_new, metrics

    def run_stream(self, stream: Stream) -> dict[str, Any]:
        """Полный прогон одного пресета. Возвращает сводку."""
        blacklist = load_blacklist(self.config_dir, stream.extra_blacklist)
        budget = RunBudget(
            max_channels=min(stream.max_new_channels, self.settings.max_channels_per_run)
        )
        run_id = self.db.start_run(stream.name)

        status = RUN_STATUS_OK
        note = ""
        checked = 0
        new_count = 0
        known = self.db.known_keys()

        try:
            candidates = self.collect_candidates(stream, budget)
            log.info("Стрим %s: %d кандидатов к проверке", stream.name, len(candidates))

            for candidate in candidates:
                # дедуп до обращения к API — не тратим запросы на известные каналы
                if candidate.key() in known:
                    log.debug("Пропуск (уже в базе): %s", candidate.key())
                    continue
                try:
                    budget.check()
                except ChannelBudgetExceeded as exc:
                    status = RUN_STATUS_BUDGET
                    note = str(exc)
                    break

                result = self.process_candidate(candidate, stream, blacklist, run_id)
                checked += 1
                if result is None:
                    continue

                _channel_id, is_new, _metrics = result
                known.add(candidate.key())
                if is_new:
                    new_count += 1
                    budget.consume()

        except RunAborted as exc:
            status = RUN_STATUS_LIMITED
            note = str(exc)
            log.error("Прогон стрима %s остановлен: %s", stream.name, exc)

        self.db.finish_run(run_id, status, checked, new_count, note)
        return {
            "stream": stream.name,
            "status": status,
            "checked": checked,
            "new": new_count,
            "note": note,
            "run_id": run_id,
            "aborted": status == RUN_STATUS_LIMITED,
        }

    def rescan_stale(self, streams: dict[str, Stream], older_than_days: int = 30,
                     limit: int = 100) -> dict[str, Any]:
        """Фоновое пересканирование каналов старше N дней (раздел 7 ТЗ)."""
        rows = self.db.stale_channels(older_than_days=older_than_days, limit=limit)
        log.info("Пересканирование: %d каналов старше %d дней", len(rows), older_than_days)

        updated = 0
        status = RUN_STATUS_OK
        note = ""
        run_id = self.db.start_run("rescan")

        try:
            for row in rows:
                if not row["username"]:
                    continue
                stream = streams.get(row["stream"])
                if stream is None:
                    # пресет удалили из yaml — обновляем по дефолтным порогам
                    stream = Stream(name=row["stream"] or "unknown")
                blacklist = load_blacklist(self.config_dir, stream.extra_blacklist)
                candidate = Candidate(
                    username=row["username"],
                    channel_id=row["channel_id"],
                    method=row["method"] or "rescan",
                    source_channel=row["source_channel"],
                )
                if self.process_candidate(candidate, stream, blacklist, run_id):
                    updated += 1
        except RunAborted as exc:
            status = RUN_STATUS_LIMITED
            note = str(exc)
            log.error("Пересканирование остановлено: %s", exc)

        self.db.finish_run(run_id, status, len(rows), updated, note)
        return {"stream": "rescan", "status": status, "checked": len(rows),
                "new": 0, "updated": updated, "note": note,
                "aborted": status == RUN_STATUS_LIMITED}


def export_dir_for_today(base: str | Path) -> Path:
    """Папка exports/YYYY-MM-DD/ для ежедневного cron-режима."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(base) / today
    path.mkdir(parents=True, exist_ok=True)
    return path
