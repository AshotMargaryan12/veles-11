"""Фильтры канала перед выгрузкой (раздел 5 ТЗ).

Фильтр никогда не удаляет канал из базы — он только помечает его как
не прошедший, чтобы не проверять повторно и не мешать дедупу.
"""

from __future__ import annotations

from typing import Any

from .config import Stream


def check_filters(metrics: dict[str, Any], stream: Stream) -> tuple[bool, list[str]]:
    """Проверяет метрики против порогов пресета.

    Возвращает (прошёл, список причин отсева). Стоп-маркеры отсекают канал
    всегда; пороги живости/ER не применяются в режиме longlist.
    """
    reasons: list[str] = []

    if metrics.get("blacklist_hit"):
        hits = ", ".join(metrics.get("blacklist_markers") or [])
        reasons.append(f"blacklist_hit: {hits}")
        # стоп-маркер — безусловный отказ, дальше проверять нечего
        return False, reasons

    subs = int(metrics.get("subscribers") or 0)
    if subs < stream.min_subscribers:
        reasons.append(f"subscribers {subs} < {stream.min_subscribers}")
    elif subs > stream.max_subscribers:
        reasons.append(f"subscribers {subs} > {stream.max_subscribers}")

    language = metrics.get("language")
    if stream.languages and language not in stream.languages:
        reasons.append(f"language {language} not in {stream.languages}")

    if not stream.longlist_mode:
        er = float(metrics.get("er") or 0)
        comments_alive = bool(metrics.get("comments_enabled")) and float(
            metrics.get("avg_reactions") or 0
        ) >= stream.min_avg_reactions
        # ER >= порога ИЛИ живые комментарии — отсекаем мёртвых и накрученных
        if er < stream.min_er and not comments_alive:
            reasons.append(
                f"er {er} < {stream.min_er} и нет живых комментариев "
                f"(реакции {metrics.get('avg_reactions')})"
            )

        days = metrics.get("days_since_last_post")
        if days is None:
            reasons.append("нет постов")
        elif days > stream.max_days_since_post:
            reasons.append(f"последний пост {days} дн. назад > {stream.max_days_since_post}")

        posts_30d = int(metrics.get("posts_last_30d") or 0)
        if posts_30d < stream.min_posts_30d:
            reasons.append(f"постов за 30 дней {posts_30d} < {stream.min_posts_30d}")

    return not reasons, reasons
