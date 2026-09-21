"""Расписание батчей (раздел 4 ТЗ)."""

from __future__ import annotations

import logging

import pytest

from crmai.scheduler import build_scheduler, digest_job, quality_job, status_job
from crmai.service import build_services

pytest.importorskip("apscheduler")


class DummyScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, args=None, id=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "id": id, **kwargs})


@pytest.fixture
def services(crm_settings, crm_db):
    return build_services(
        crm_settings, db=crm_db, use_llm=False, use_amo=False, use_telegram=False
    )


def test_three_jobs_on_workdays(crm_settings, services):
    crm_settings.batch_hour = 19
    scheduler = build_scheduler(services, scheduler=DummyScheduler())

    by_id = {job["id"]: job for job in scheduler.jobs}
    assert set(by_id) == {"status", "quality", "digest"}
    assert [by_id[name]["minute"] for name in ("status", "quality", "digest")] == [0, 30, 45]
    assert all(job["hour"] == 19 for job in scheduler.jobs)
    assert all(job["day_of_week"] == "mon-fri" for job in scheduler.jobs)


def test_batch_hour_is_configurable(crm_settings, services):
    crm_settings.batch_hour = 21
    scheduler = build_scheduler(services, scheduler=DummyScheduler())

    assert all(job["hour"] == 21 for job in scheduler.jobs)


def test_jobs_skip_when_llm_not_configured(services, caplog):
    # LLM не настроена: батчи не падают, а пишут предупреждение
    status_job(services)
    quality_job(services)
    assert "LLM не настроена" in caplog.text


def test_digest_job_without_telegram_only_logs(services, caplog):
    caplog.set_level(logging.INFO, logger="crmai.scheduler")
    digest_job(services)
    assert "Дайджест не отправлен" in caplog.text


def test_real_scheduler_registers_cron_triggers(services):
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = build_scheduler(services, scheduler=BackgroundScheduler())
    triggers = {job.id: str(job.trigger) for job in scheduler.get_jobs()}

    assert "day_of_week='mon-fri'" in triggers["status"]
    assert "minute='45'" in triggers["digest"]
