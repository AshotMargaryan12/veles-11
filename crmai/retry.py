"""Ретраи к внешним сервисам: 3 попытки с экспоненциальной задержкой (раздел 4.5 ТЗ)."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterable, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 2.0


class RetryExhausted(RuntimeError):
    """Все попытки исчерпаны. В сообщении — последняя ошибка."""


def retry_call(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    retry_on: Iterable[type[BaseException]] = (Exception,),
    give_up_on: Iterable[type[BaseException]] = (),
    sleeper: Optional[Callable[[float], None]] = None,
    what: str = "запрос",
) -> T:
    """Вызывает func, повторяя при ошибке: 2с, 4с, 8с...

    `give_up_on` — ошибки, при которых повтор бессмыслен (например, 400 от amo).
    """
    retry_on = tuple(retry_on)
    give_up_on = tuple(give_up_on)
    # Берём time.sleep в момент вызова, а не в дефолте: так его можно подменить в тестах
    sleep = sleeper or time.sleep
    last: Optional[BaseException] = None

    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func()
        except give_up_on:  # type: ignore[misc]
            raise
        except retry_on as exc:  # type: ignore[misc]
            last = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            log.warning(
                "%s: попытка %d/%d не удалась (%s), повтор через %.0f с",
                what, attempt, attempts, exc, delay,
            )
            sleep(delay)

    raise RetryExhausted(f"{what}: {attempts} попыток не удались — {last}") from last


def safe_call(func: Callable[[], T], default: Any = None, what: str = "операция") -> Any:
    """Выполняет func, гасит любую ошибку и возвращает default. Для необязательных шагов."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 — сознательно глушим
        log.warning("%s не выполнена: %s", what, exc)
        return default
