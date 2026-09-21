"""Расписание батчей: APScheduler поверх BATCH_HOUR и TZ (раздел 4 ТЗ).

По будням:
  BATCH_HOUR:00 — уровень 1, авто-статусы сделок;
  BATCH_HOUR:30 — уровень 2, контроль качества исходящих;
  BATCH_HOUR:45 — дайджест руководителю.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .analysis import run_status_batch
from .clock import tzinfo
from .digest import build_digest
from .prompts import Prompts
from .quality import run_quality_batch
from .service import Services

log = logging.getLogger(__name__)

WORKDAYS = "mon-fri"


def status_job(services: Services) -> None:
    if services.llm is None:
        log.warning("Батч уровня 1 пропущен: LLM не настроена")
        return
    prompts = services.prompts or Prompts.load(services.settings.prompts_dir)
    run_status_batch(services.settings, services.db, services.llm, prompts, amo=services.amo)


def quality_job(services: Services) -> None:
    if services.llm is None:
        log.warning("Батч уровня 2 пропущен: LLM не настроена")
        return
    prompts = services.prompts or Prompts.load(services.settings.prompts_dir)
    run_quality_batch(
        services.settings,
        services.db,
        services.llm,
        prompts,
        amo=services.amo,
        notifier=services.notifier,
    )


def digest_job(services: Services) -> None:
    text = build_digest(services.settings, services.db)
    if services.notifier is None:
        log.info("Дайджест не отправлен (Telegram не настроен):\n%s", text)
        return
    services.notifier.broadcast(text)


def build_scheduler(services: Services, scheduler: Optional[Any] = None) -> Any:
    """Регистрирует три задачи. APScheduler импортируется лениво."""
    if scheduler is None:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone=tzinfo(services.settings))

    hour = services.settings.batch_hour
    scheduler.add_job(
        status_job, "cron", args=[services], id="status",
        day_of_week=WORKDAYS, hour=hour, minute=0, misfire_grace_time=3600,
    )
    scheduler.add_job(
        quality_job, "cron", args=[services], id="quality",
        day_of_week=WORKDAYS, hour=hour, minute=30, misfire_grace_time=3600,
    )
    scheduler.add_job(
        digest_job, "cron", args=[services], id="digest",
        day_of_week=WORKDAYS, hour=hour, minute=45, misfire_grace_time=3600,
    )
    log.info(
        "Расписание: уровень 1 в %02d:00, уровень 2 в %02d:30, дайджест в %02d:45 (%s, будни)",
        hour, hour, hour, services.settings.tz,
    )
    return scheduler
