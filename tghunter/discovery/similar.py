"""Метод 2: нативные рекомендации Telegram к эталонным каналам.

Главный метод хантинга — lookalike от каналов, которые уже конвертят.
"""

from __future__ import annotations

import logging
from typing import Any

from ..db import normalize_username
from ..models import Candidate

log = logging.getLogger(__name__)


def similar_candidates(
    gateway: Any, seeds: list[str], limit_per_seed: int = 50
) -> list[Candidate]:
    """Для каждого эталонного канала тянет рекомендации Telegram."""
    found: dict[str, Candidate] = {}
    seen_seeds: set[str] = set()

    for raw in seeds:
        username = normalize_username(raw)
        if not username or username.lower() in seen_seeds:
            continue
        seen_seeds.add(username.lower())

        candidates = gateway.similar_channels(username, limit=limit_per_seed)
        log.info("similar: @%s -> %d каналов", username, len(candidates))
        for candidate in candidates:
            if candidate.username and candidate.username.lower() in seen_seeds:
                continue  # не предлагаем сам эталонный канал
            found.setdefault(candidate.key(), candidate)

    return list(found.values())
