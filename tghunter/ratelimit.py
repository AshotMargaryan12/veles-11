"""Ограничения Telegram и безопасность аккаунта (раздел 9 ТЗ).

* глобальный rate limit: не чаще одного запроса в 2-3 секунды с джиттером;
* FloodWait: спать указанное время +10%, логировать;
* два FloodWait подряд — аварийная остановка прогона;
* лимит новых каналов за прогон;
* пауза 30-60 секунд между методами поиска.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Запас поверх времени, которое требует Telegram при FloodWait
FLOODWAIT_MARGIN = 1.1
# FloodWait длиннее этого считаем жёстким баном и не отсиживаем
FLOODWAIT_HARD_LIMIT_SEC = 3600


class RunAborted(Exception):
    """Прогон остановлен по лимитам Telegram."""


class ChannelBudgetExceeded(Exception):
    """Достигнут MAX_CHANNELS_PER_RUN."""


@dataclass
class RateLimiter:
    """Троттлинг запросов + учёт FloodWait."""

    min_interval: float = 2.0
    max_interval: float = 3.0
    floodwait_abort_streak: int = 2
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    _last_call: Optional[float] = field(default=None, init=False)
    floodwait_streak: int = field(default=0, init=False)
    floodwait_total: int = field(default=0, init=False)
    floodwait_seconds: float = field(default=0.0, init=False)
    requests: int = field(default=0, init=False)

    def wait(self) -> None:
        """Держит паузу между запросами к API."""
        target = random.uniform(self.min_interval, self.max_interval)
        if self._last_call is not None:
            elapsed = self.clock() - self._last_call
            if elapsed < target:
                self.sleeper(target - elapsed)
        self._last_call = self.clock()
        self.requests += 1

    def note_success(self) -> None:
        """Успешный запрос обнуляет серию FloodWait."""
        self.floodwait_streak = 0

    def note_floodwait(self, seconds: float) -> float:
        """Обрабатывает FloodWaitError: спит seconds +10%, считает серию.

        Возвращает фактическое время сна. Бросает RunAborted, если FloodWait
        пришёл подряд floodwait_abort_streak раз или требует слишком долгой паузы.
        """
        self.floodwait_streak += 1
        self.floodwait_total += 1
        sleep_for = float(seconds) * FLOODWAIT_MARGIN
        self.floodwait_seconds += sleep_for

        log.warning(
            "FloodWait %.0f сек (серия %d/%d), спим %.0f сек",
            seconds,
            self.floodwait_streak,
            self.floodwait_abort_streak,
            sleep_for,
        )

        if seconds >= FLOODWAIT_HARD_LIMIT_SEC:
            raise RunAborted(
                f"FloodWait {seconds:.0f} сек — слишком долго, останавливаем прогон"
            )
        if self.floodwait_streak >= self.floodwait_abort_streak:
            raise RunAborted(
                f"{self.floodwait_streak} FloodWait подряд — остановлен по лимитам"
            )

        self.sleeper(sleep_for)
        self._last_call = self.clock()
        return sleep_for

    def pause_between_methods(self, min_sec: float, max_sec: float) -> float:
        """Пауза 30-60 секунд между методами поиска."""
        delay = random.uniform(min_sec, max_sec)
        log.info("Пауза между методами: %.0f сек", delay)
        self.sleeper(delay)
        self._last_call = self.clock()
        return delay


@dataclass
class RunBudget:
    """Лимит новых каналов за прогон (MAX_CHANNELS_PER_RUN)."""

    max_channels: int = 200
    used: int = 0

    @property
    def left(self) -> int:
        return max(0, self.max_channels - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_channels

    def consume(self, count: int = 1) -> None:
        self.used += count
        if self.exhausted:
            log.info("Достигнут лимит новых каналов за прогон: %d", self.max_channels)

    def check(self) -> None:
        if self.exhausted:
            raise ChannelBudgetExceeded(
                f"Достигнут MAX_CHANNELS_PER_RUN={self.max_channels}"
            )
