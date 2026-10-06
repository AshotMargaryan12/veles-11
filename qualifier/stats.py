"""Метрики площадки из сырых данных (раздел 4 ТЗ).

ER = медиана просмотров / подписчики: медиана устойчивее к вирусным выбросам.
Посты моложе `views_min_age_hours` в просмотры не идут — они ещё набирают охват.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Optional

from .ads import find_contact, scan_post
from .config import Thresholds
from .models import (
    KIND_GROUP,
    PLATFORM_YOUTUBE,
    ContentItem,
    PlatformData,
    PlatformMetrics,
)

SHORTS_MAX_SEC = 60
MIN_VIEWS_SAMPLE = 3


def _max_opt(a: Optional[int], b: Optional[int]) -> Optional[int]:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def merge_albums(items: list[ContentItem]) -> list[ContentItem]:
    """Сообщения одного альбома Telegram -> один пост (просмотры у них общие)."""
    merged: list[ContentItem] = []
    by_group: dict[str, ContentItem] = {}
    for item in items:
        if not item.group_id:
            merged.append(item)
            continue
        current = by_group.get(item.group_id)
        if current is None:
            clone = ContentItem(**{**item.__dict__})
            by_group[item.group_id] = clone
            merged.append(clone)
            continue
        current.views = _max_opt(current.views, item.views)
        current.reactions = _max_opt(current.reactions, item.reactions)
        current.comments = _max_opt(current.comments, item.comments)
        if item.text and item.text not in current.text:
            current.text = f"{current.text}\n{item.text}".strip()
        if item.date and (current.date is None or item.date > current.date):
            current.date = item.date
    return merged


def sorted_items(data: PlatformData) -> list[ContentItem]:
    """Посты от новых к старым, альбомы схлопнуты, без дат — в конце."""
    items = merge_albums(list(data.items))
    dated = sorted((i for i in items if i.date), key=lambda i: i.date, reverse=True)
    return dated + [i for i in items if not i.date]


def _views_pool(
    data: PlatformData, items: list[ContentItem], thresholds: Thresholds, now: datetime
) -> tuple[list[ContentItem], int, int]:
    """Посты для статистики просмотров. -> (пул, исключено молодых, исключено Shorts)."""
    cfg = thresholds.platform(data.platform)
    window = int(cfg.get("views_window", 20))
    min_age_days = float(cfg.get("views_min_age_hours", 24)) / 24.0

    with_views = [i for i in items if i.views is not None]

    shorts_excluded = 0
    if data.platform == PLATFORM_YOUTUBE:
        long_form = [i for i in with_views if not (i.duration_sec and i.duration_sec <= SHORTS_MAX_SEC)]
        if len(long_form) >= MIN_VIEWS_SAMPLE and len(long_form) < len(with_views):
            shorts_excluded = len(with_views) - len(long_form)
            with_views = long_form

    def is_mature(item: ContentItem) -> bool:
        age = item.age_days(now)
        return age is None or age >= min_age_days

    mature = [i for i in with_views if is_mature(i)]
    if len(mature) >= MIN_VIEWS_SAMPLE:
        return mature[:window], len(with_views) - len(mature), shorts_excluded
    # молодых слишком много — лучше неполные цифры, чем никаких
    return with_views[:window], 0, shorts_excluded


def compute_metrics(data: PlatformData, thresholds: Thresholds, now: datetime) -> PlatformMetrics:
    metrics = PlatformMetrics(platform=data.platform, followers=data.followers)
    metrics.has_community_chat = data.has_community_chat
    metrics.community_members = data.community_members
    metrics.contact = find_contact(data.description, data.username or data.handle)
    if not data.ok:
        return metrics

    items = sorted_items(data)
    metrics.items_total = len(items)

    # --- активность ------------------------------------------------------
    window_days = int(thresholds.get("frequency_window_days", 30))
    dated = [i for i in items if i.date]
    if dated:
        metrics.last_post_at = dated[0].date
        metrics.days_since_last_post = max(0, int((now - dated[0].date).total_seconds() // 86400))
        metrics.posts_30d = sum(1 for i in dated if (i.age_days(now) or 0) <= window_days)
        fetched_all_in_window = (dated[-1].age_days(now) or 0) <= window_days
        limit = data.items_limit
        metrics.posts_30d_capped = bool(
            fetched_all_in_window and limit and len(data.items) >= limit
        )

    # --- просмотры и ER --------------------------------------------------
    if data.kind != KIND_GROUP:
        pool, young, shorts = _views_pool(data, items, thresholds, now)
        metrics.young_posts_excluded = young
        metrics.shorts_excluded = shorts
        views = [int(i.views) for i in pool if i.views is not None]
        metrics.views_sample = len(views)
        if views:
            mean = statistics.fmean(views)
            median = statistics.median(views)
            metrics.avg_views = round(mean, 1)
            metrics.median_views = int(round(median))
            if len(views) >= 2 and mean > 0:
                metrics.views_cv = round(statistics.pstdev(views) / mean, 3)
            if median > 0:
                metrics.max_to_median = round(max(views) / median, 2)
            if data.followers:
                metrics.er = round(median / data.followers * 100, 1)
                metrics.er_avg = round(mean / data.followers * 100, 1)

        reactions = [i.reactions for i in pool if i.reactions is not None]
        if reactions:
            metrics.avg_reactions = round(statistics.fmean(reactions), 1)
        comments = [i.comments for i in pool if i.comments is not None]
        if comments:
            metrics.avg_comments = round(statistics.fmean(comments), 1)

    # --- реклама ---------------------------------------------------------
    ads_window = int(thresholds.get("ads_window_posts", 30))
    recent = items[:ads_window]
    metrics.ads_window = len(recent)
    markers: list[str] = []
    for item in recent:
        scan = scan_post(item.text)
        if scan.is_ad:
            metrics.ads_posts += 1
            for marker in scan.markers:
                if marker not in markers:
                    markers.append(marker)
        if scan.exchange_ref:
            metrics.competitor_ref_posts += 1
    metrics.ads_markers = markers
    return metrics


def primary_key(platforms: list[PlatformData]) -> Optional[str]:
    """Крупнейшая площадка с данными: каналы/профили важнее чатов."""
    from .models import platform_key

    ok = [p for p in platforms if p.ok]
    if not ok:
        return None
    ranked = sorted(
        ok,
        key=lambda p: (p.kind != KIND_GROUP, p.followers or 0),
        reverse=True,
    )
    return platform_key(ranked[0])
