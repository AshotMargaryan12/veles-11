"""Сборщики публичных данных площадок.

Каждый сборщик реализует `collect(ref: PlatformRef) -> PlatformData` и никогда
не бросает исключение наружу из-за ограничений доступа: вместо этого
возвращает PlatformData со статусом unavailable/not_found и понятной причиной.
Исключение — RunAborted (серия FloodWait): это сигнал остановить весь прогон.
"""

from __future__ import annotations

from typing import Protocol

from ..models import PlatformData, PlatformRef


class Collector(Protocol):
    platform: str

    def collect(self, ref: PlatformRef) -> PlatformData:  # pragma: no cover - протокол
        ...

    def close(self) -> None:  # pragma: no cover - протокол
        ...
