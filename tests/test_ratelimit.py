"""Ограничения Telegram и безопасность аккаунта (раздел 9 ТЗ)."""

from __future__ import annotations

import pytest

from tghunter.ratelimit import (
    ChannelBudgetExceeded,
    RateLimiter,
    RunAborted,
    RunBudget,
)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_limiter(**kwargs):
    sleeps: list[float] = []
    clock = Clock()

    def sleeper(seconds):
        sleeps.append(seconds)
        clock.t += seconds

    defaults = dict(min_interval=2.0, max_interval=3.0, floodwait_abort_streak=2)
    defaults.update(kwargs)
    return RateLimiter(sleeper=sleeper, clock=clock, **defaults), sleeps, clock


def test_first_request_is_not_delayed():
    limiter, sleeps, _ = make_limiter()
    limiter.wait()
    assert sleeps == []


def test_requests_are_throttled_to_2_3_seconds():
    limiter, sleeps, _ = make_limiter()
    for _ in range(5):
        limiter.wait()
    assert len(sleeps) == 4
    assert all(2.0 <= s <= 3.0 for s in sleeps)
    assert limiter.requests == 5


def test_elapsed_time_counts_towards_interval():
    limiter, sleeps, clock = make_limiter(min_interval=3.0, max_interval=3.0)
    limiter.wait()
    clock.t += 1.0  # запрос сам занял секунду
    limiter.wait()
    assert sleeps == [2.0]


def test_floodwait_sleeps_with_10_percent_margin():
    limiter, sleeps, _ = make_limiter()
    slept = limiter.note_floodwait(60)
    assert slept == pytest.approx(66.0)
    assert sleeps == [pytest.approx(66.0)]
    assert limiter.floodwait_total == 1


def test_two_floodwaits_in_a_row_abort_the_run():
    limiter, _, _ = make_limiter(floodwait_abort_streak=2)
    limiter.note_floodwait(10)
    with pytest.raises(RunAborted, match="остановлен по лимитам"):
        limiter.note_floodwait(10)


def test_successful_request_resets_floodwait_streak():
    limiter, _, _ = make_limiter(floodwait_abort_streak=2)
    limiter.note_floodwait(10)
    limiter.note_success()
    limiter.note_floodwait(10)  # снова первый в серии — не должен ронять прогон
    assert limiter.floodwait_streak == 1


def test_very_long_floodwait_aborts_immediately():
    limiter, sleeps, _ = make_limiter(floodwait_abort_streak=5)
    with pytest.raises(RunAborted, match="слишком долго"):
        limiter.note_floodwait(7200)
    assert sleeps == []  # два часа мы не спим


def test_pause_between_methods():
    limiter, sleeps, _ = make_limiter()
    delay = limiter.pause_between_methods(30, 60)
    assert 30 <= delay <= 60
    assert sleeps == [delay]


def test_run_budget_limits_new_channels():
    budget = RunBudget(max_channels=3)
    for _ in range(3):
        budget.check()
        budget.consume()
    assert budget.exhausted and budget.left == 0
    with pytest.raises(ChannelBudgetExceeded, match="MAX_CHANNELS_PER_RUN=3"):
        budget.check()
