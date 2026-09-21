"""Метод 1: глобальный поиск Telegram по словарю ключевых слов."""

from __future__ import annotations

import logging
from typing import Any

from ..models import Candidate

log = logging.getLogger(__name__)


def keyword_candidates(
    gateway: Any, keywords: list[str], limit_per_query: int = 30
) -> list[Candidate]:
    """Проходит по всем ключевым словам пресета и собирает публичные каналы."""
    found: dict[str, Candidate] = {}

    for keyword in keywords:
        keyword = keyword.strip()
        if not keyword:
            continue
        candidates = gateway.search_keyword(keyword, limit=limit_per_query)
        log.info("keywords: '%s' -> %d каналов", keyword, len(candidates))
        for candidate in candidates:
            found.setdefault(candidate.key(), candidate)

    return list(found.values())
