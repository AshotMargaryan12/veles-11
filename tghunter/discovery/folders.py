"""Метод 4: разбор папок-подборок t.me/addlist/*.

Список ссылок подаётся файлом (config/addlists.txt) — читаем каналы папки,
не вступая в неё.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Candidate

log = logging.getLogger(__name__)


def folder_candidates(gateway: Any, addlist_urls: list[str]) -> list[Candidate]:
    found: dict[str, Candidate] = {}

    for url in addlist_urls:
        url = url.strip()
        if not url or "addlist" not in url:
            continue
        candidates = gateway.folder_channels(url)
        log.info("folders: %s -> %d каналов", url, len(candidates))
        for candidate in candidates:
            found.setdefault(candidate.key(), candidate)

    return list(found.values())
