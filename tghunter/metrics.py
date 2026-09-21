"""Сбор метрик канала: чистый интерфейс `ChannelSnapshot -> dict`.

Каждая группа метрик — отдельный провайдер с сигнатурой
`(snapshot, context) -> dict`. Провайдеры регистрируются в реестре, поэтому
новый модуль (например, LLM-классификация тематики, раздел 13 ТЗ) добавляется
одной функцией и не требует правок остального кода.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .language import detect_language
from .models import ChannelSnapshot
from .textscan import (
    detect_monetization,
    extract_contact,
    find_blacklist_markers,
)

# Посты старше этого возраста не учитываются в ER (раздел 4.2 ТЗ)
ER_MAX_POST_AGE_DAYS = 60
# Сколько последних постов берём для расчёта ER
ER_POSTS_WINDOW = 10
# Окно «частоты постинга»
FREQUENCY_WINDOW_DAYS = 30
# Окно анализа монетизации (раздел 4.6 ТЗ)
MONETIZATION_POSTS_WINDOW = 30

MetricsProvider = Callable[[ChannelSnapshot, dict[str, Any]], dict[str, Any]]

_REGISTRY: list[tuple[str, MetricsProvider]] = []


def register_provider(name: str, provider: MetricsProvider) -> None:
    """Добавляет провайдер метрик в конец цепочки."""
    _REGISTRY.append((name, provider))


def registered_providers() -> list[str]:
    return [name for name, _ in _REGISTRY]


def _recent_posts(snapshot: ChannelSnapshot, now: datetime) -> list:
    """Посты, отсортированные от новых к старым."""
    return sorted(snapshot.posts, key=lambda p: p.date, reverse=True)


def basic_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.1: username, title, описание, подписчики, дата последнего поста."""
    posts = ctx["posts"]
    last_post_date = posts[0].date if posts else None
    return {
        "channel_id": snapshot.channel_id,
        "username": snapshot.username,
        "title": snapshot.title,
        "about": snapshot.about,
        "subscribers": snapshot.subscribers,
        "link": snapshot.link,
        "last_post_date": last_post_date.isoformat() if last_post_date else None,
        "posts_analyzed": len(posts),
    }


def engagement_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.2: ER по среднему просмотров + медиана просмотров."""
    now: datetime = ctx["now"]
    fresh = [
        p
        for p in ctx["posts"]
        if p.views is not None and p.age_days(now) <= ER_MAX_POST_AGE_DAYS
    ][:ER_POSTS_WINDOW]

    views = [p.views for p in fresh if p.views is not None]
    avg_views = statistics.fmean(views) if views else 0.0
    median_views = int(statistics.median(views)) if views else 0

    subs = snapshot.subscribers or 0
    er = round(avg_views / subs * 100, 2) if subs > 0 and views else 0.0
    er_median = round(median_views / subs * 100, 2) if subs > 0 and views else 0.0

    return {
        "avg_views": round(avg_views, 1),
        "median_views": median_views,
        "er": er,
        "er_median": er_median,
        "er_sample_size": len(views),
    }


def liveness_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.3: дней с последнего поста и частота постинга за 30 дней."""
    now: datetime = ctx["now"]
    posts = ctx["posts"]
    if not posts:
        return {"days_since_last_post": None, "posts_last_30d": 0}

    days_since = int(posts[0].age_days(now))
    posts_30d = sum(1 for p in posts if p.age_days(now) <= FREQUENCY_WINDOW_DAYS)
    return {"days_since_last_post": days_since, "posts_last_30d": posts_30d}


def community_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.4: среднее реакций и наличие комментариев (linked chat)."""
    now: datetime = ctx["now"]
    fresh = [p for p in ctx["posts"] if p.age_days(now) <= ER_MAX_POST_AGE_DAYS][
        :ER_POSTS_WINDOW
    ]
    avg_reactions = (
        round(statistics.fmean([p.reactions for p in fresh]), 1) if fresh else 0.0
    )
    return {
        "avg_reactions": avg_reactions,
        "comments_enabled": bool(snapshot.has_linked_chat),
    }


def language_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.5: язык канала по описанию и последним постам."""
    texts = [snapshot.title, snapshot.about]
    texts += [p.text for p in ctx["posts"][:ER_POSTS_WINDOW]]
    return {"language": detect_language(texts)}


def monetization_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.6: продаёт ли канал рекламу."""
    texts = [p.text for p in ctx["posts"][:MONETIZATION_POSTS_WINDOW]]
    texts.append(snapshot.about)
    markers = detect_monetization(texts)
    return {"monetized": bool(markers), "monetization_markers": markers}


def contact_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.7: контакт для связи из описания канала."""
    contact = extract_contact(snapshot.about)
    return {"contact": contact, "has_contact": bool(contact)}


def blacklist_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Раздел 4.8: стоп-маркеры в названии/описании/постах."""
    markers = ctx.get("blacklist_markers") or []
    texts = [snapshot.title, snapshot.about]
    texts += [p.text for p in ctx["posts"][:MONETIZATION_POSTS_WINDOW]]
    hits = find_blacklist_markers(texts, markers)
    return {"blacklist_hit": bool(hits), "blacklist_markers": hits}


def source_provider(snapshot: ChannelSnapshot, ctx: dict[str, Any]) -> dict[str, Any]:
    """Откуда канал найден: метод и канал-источник (раздел 7 ТЗ)."""
    return {
        "method": snapshot.method,
        "source_channel": snapshot.source_channel,
        "stream": ctx.get("stream"),
    }


for _name, _provider in (
    ("basic", basic_provider),
    ("engagement", engagement_provider),
    ("liveness", liveness_provider),
    ("community", community_provider),
    ("language", language_provider),
    ("monetization", monetization_provider),
    ("contact", contact_provider),
    ("blacklist", blacklist_provider),
    ("source", source_provider),
):
    register_provider(_name, _provider)


def collect_metrics(
    snapshot: ChannelSnapshot,
    blacklist_markers: Optional[list[str]] = None,
    stream: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Главная точка входа слоя метрик: канал -> плоский dict метрик."""
    now = now or datetime.now(timezone.utc)
    ctx: dict[str, Any] = {
        "now": now,
        "posts": _recent_posts(snapshot, now),
        "blacklist_markers": blacklist_markers or [],
        "stream": stream,
    }

    metrics: dict[str, Any] = {}
    for name, provider in _REGISTRY:
        try:
            metrics.update(provider(snapshot, ctx))
        except Exception as exc:  # провайдер не должен ронять весь прогон
            metrics.setdefault("metrics_errors", []).append(f"{name}: {exc}")

    metrics["checked_at"] = now.isoformat()
    return metrics
