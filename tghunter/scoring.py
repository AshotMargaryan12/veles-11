"""Скоринг канала 0-100 и разбивка баллов (раздел 5 ТЗ)."""

from __future__ import annotations

from typing import Any

from .models import METHOD_SIMILAR

# Веса скоринга. Сумма = 100.
WEIGHT_MONETIZED = 30       # канал уже продаёт рекламу
WEIGHT_SIMILAR = 25         # найден через similar к эталонному каналу
WEIGHT_HIGH_ER = 20         # ER выше 7%
WEIGHT_CONTACT = 10         # есть контакт в описании
WEIGHT_LIVE_COMMENTS = 10   # комментарии включены и живые
WEIGHT_FREQUENCY = 5        # 8+ постов в месяц

HIGH_ER_THRESHOLD = 7.0
LIVE_REACTIONS_THRESHOLD = 5.0
FREQUENCY_THRESHOLD = 8

MAX_SCORE = (
    WEIGHT_MONETIZED
    + WEIGHT_SIMILAR
    + WEIGHT_HIGH_ER
    + WEIGHT_CONTACT
    + WEIGHT_LIVE_COMMENTS
    + WEIGHT_FREQUENCY
)


def score_channel(metrics: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Считает score и разбивку по слагаемым.

    Возвращает (score, breakdown) — и то, и другое пишется в выгрузку.
    """
    breakdown: dict[str, int] = {}

    if metrics.get("monetized"):
        breakdown["monetized"] = WEIGHT_MONETIZED

    if metrics.get("method") == METHOD_SIMILAR:
        breakdown["similar"] = WEIGHT_SIMILAR

    if float(metrics.get("er") or 0) > HIGH_ER_THRESHOLD:
        breakdown["high_er"] = WEIGHT_HIGH_ER

    if metrics.get("has_contact") or metrics.get("contact"):
        breakdown["contact"] = WEIGHT_CONTACT

    if metrics.get("comments_enabled") and float(
        metrics.get("avg_reactions") or 0
    ) >= LIVE_REACTIONS_THRESHOLD:
        breakdown["live_comments"] = WEIGHT_LIVE_COMMENTS

    if int(metrics.get("posts_last_30d") or 0) >= FREQUENCY_THRESHOLD:
        breakdown["frequency"] = WEIGHT_FREQUENCY

    return sum(breakdown.values()), breakdown


def format_breakdown(breakdown: dict[str, int]) -> str:
    """Человекочитаемая разбивка для CSV: `monetized+30|similar+25`."""
    return "|".join(f"{key}+{value}" for key, value in breakdown.items())
