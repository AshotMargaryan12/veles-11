"""Метод 3: граф упоминаний.

Обходит последние N постов уже найденных каналов и достаёт из них форварды,
t.me-ссылки и @упоминания. Глубина обхода задаётся конфигом (по умолчанию 1
уровень от seed-каналов).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..db import normalize_username
from ..models import METHOD_MENTIONS, Candidate, ChannelSnapshot
from ..textscan import extract_usernames

log = logging.getLogger(__name__)

# Служебные username, которые упоминаются повсеместно и каналами-целями не являются
_IGNORED = {
    "telegram", "durov", "telegramtips", "premium", "gifts", "stickers",
    "botfather", "spambot",
}


def candidates_from_posts(
    snapshot: ChannelSnapshot, posts_limit: int = 50
) -> list[Candidate]:
    """Достаёт кандидатов из постов одного канала."""
    source = f"@{snapshot.username}" if snapshot.username else str(snapshot.channel_id)
    found: dict[str, Candidate] = {}
    own = (snapshot.username or "").lower()

    for post in snapshot.posts[:posts_limit]:
        # форварды из других каналов
        if post.forward_from_channel_id and post.forward_from_channel_id != snapshot.channel_id:
            candidate = Candidate(
                username=None,
                channel_id=int(post.forward_from_channel_id),
                method=METHOD_MENTIONS,
                source_channel=source,
            )
            found.setdefault(candidate.key(), candidate)

        # @упоминания и t.me-ссылки
        for username in extract_usernames(post.text):
            if username in _IGNORED or username == own:
                continue
            candidate = Candidate(
                username=username,
                method=METHOD_MENTIONS,
                source_channel=source,
            )
            found.setdefault(candidate.key(), candidate)

    return list(found.values())


def mention_candidates(
    gateway: Any,
    seeds: list[str],
    posts_per_channel: int = 50,
    depth: int = 1,
    max_seeds: Optional[int] = None,
) -> list[Candidate]:
    """Обход графа упоминаний от seed-каналов на заданную глубину."""
    found: dict[str, Candidate] = {}
    visited: set[str] = set()

    frontier = [normalize_username(s) for s in seeds]
    frontier = [s for s in frontier if s]
    if max_seeds:
        frontier = frontier[:max_seeds]

    for level in range(max(1, depth)):
        next_frontier: list[str] = []
        for username in frontier:
            key = username.lower()
            if key in visited:
                continue
            visited.add(key)

            candidates = gateway.channel_mentions(username, posts_limit=posts_per_channel)
            log.info(
                "mentions[%d]: @%s -> %d кандидатов", level + 1, username, len(candidates)
            )
            for candidate in candidates:
                if found.setdefault(candidate.key(), candidate) is candidate:
                    if candidate.username:
                        next_frontier.append(candidate.username)

        if level + 1 >= depth:
            break
        frontier = next_frontier
        if max_seeds:
            frontier = frontier[:max_seeds]

    return list(found.values())
