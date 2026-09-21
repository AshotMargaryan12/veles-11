"""Метод 5: ручной ввод.

Файл со списком username/ссылок (CSV или plain text) — каналы просто
обогащаются метриками. Для лонглистов из внешних источников (TGStat и т.п.).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..db import normalize_username
from ..models import METHOD_MANUAL, Candidate

log = logging.getLogger(__name__)

# Колонки, в которых ищем username, если на вход подан CSV
_USERNAME_COLUMNS = ("username", "link", "url", "channel", "канал", "ссылка")


def read_channel_list(path: str | Path) -> list[str]:
    """Читает username из CSV (с заголовком) или из plain-text списка."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл: {path}")

    text = path.read_text(encoding="utf-8-sig")
    usernames: list[str] = []

    if path.suffix.lower() == ".csv" or "," in text.split("\n")[0]:
        reader = csv.DictReader(text.splitlines())
        columns = [c for c in (reader.fieldnames or []) if c]
        target = next(
            (c for c in columns if c.strip().lower() in _USERNAME_COLUMNS),
            columns[0] if columns else None,
        )
        if target:
            for row in reader:
                username = normalize_username(row.get(target) or "")
                if username:
                    usernames.append(username)
            return _dedup(usernames)

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        username = normalize_username(line)
        if username:
            usernames.append(username)
    return _dedup(usernames)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item.lower() in seen:
            continue
        seen.add(item.lower())
        result.append(item)
    return result


def manual_candidates(path: str | Path) -> list[Candidate]:
    usernames = read_channel_list(path)
    log.info("manual: %s -> %d каналов", path, len(usernames))
    return [
        Candidate(username=u, method=METHOD_MANUAL, source_channel=str(path))
        for u in usernames
    ]
