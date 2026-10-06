"""Эвристика гео аудитории с уверенностью (раздел 4.4 ТЗ).

Прямой статистики аудитории нет, поэтому гео складывается из сигналов:
  1. язык контента (основной);
  2. страна, указанная в профиле площадки;
  3. местные реалии: валюты, банки, платёжки, города (geo_markers.yaml);
  4. для русскоязычных — маркеры, различающие RU / UA / KZ / BY;
  5. подсказка LLM с обоснованием (если LLM-слой включён) — как один из сигналов.
"""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from typing import Iterable, Optional

from .config import CountryProfile
from .lang import language_name
from .models import GeoEstimate, GeoSignal, LanguageResult

LANGUAGE_WEIGHT = 3.0
PROFILE_WEIGHT = 4.0
LLM_WEIGHT = 2.0
MARKER_DISTINCT_WEIGHT = 1.2
MARKER_POST_WEIGHT = 0.3
MARKER_CAP = 5.0
# меньше этого — сигналов слишком мало, страну не называем (например, англоязычный канал)
MIN_SCORE = 1.0


@lru_cache(maxsize=4096)
def _marker_regex(marker: str) -> re.Pattern[str]:
    if marker.startswith("re:"):
        return re.compile(marker[3:], re.IGNORECASE)
    clean = marker.strip()
    if re.fullmatch(r"[A-Za-z0-9 .'\-]+", clean):
        # латиница — целым словом: «payme» не должен находиться в «payments»
        return re.compile(r"(?<!\w)" + re.escape(clean) + r"(?!\w)", re.IGNORECASE)
    short_word = len(clean) <= 4 and re.fullmatch(r"[А-Яа-яЁёІіЇїЄєҐґЎў'.]+", clean)
    if short_word:
        # короткие кириллические основы — с начала слова: «сбер» найдёт «сбербанк», «дия» не найдёт «индия»
        return re.compile(r"(?<!\w)" + re.escape(clean), re.IGNORECASE)
    return re.compile(re.escape(marker), re.IGNORECASE)


def find_markers(texts: list[str], markers: Iterable[str]) -> dict[str, int]:
    """Маркер -> в скольких текстах найден."""
    hits: dict[str, int] = {}
    for marker in markers:
        pattern = _marker_regex(marker)
        count = sum(1 for text in texts if text and pattern.search(text))
        if count:
            hits[marker] = count
    return hits


def _display_marker(marker: str) -> str:
    """Маркер для карточки: у регэкспов убираем служебный синтаксис."""
    if not marker.startswith("re:"):
        return marker.strip()
    text = re.sub(r"\(\?<?[!=][^)]*\)", "", marker[3:])
    text = re.sub(r"\\[bdsw][?*+]?", "", text)
    return text.strip("^$()|? ") or marker[3:]


def estimate_geo(
    texts: list[str],
    language: LanguageResult,
    profiles: dict[str, CountryProfile],
    profile_countries: Optional[list[str]] = None,
    llm_country: Optional[str] = None,
    llm_evidence: Optional[list[str]] = None,
    manual: Optional[str] = None,
) -> GeoEstimate:
    """Сводит сигналы в одну оценку: страна, уверенность, расшифровка сигналов."""
    if manual:
        code = manual.upper()
        profile = profiles.get(code)
        return GeoEstimate(
            country=code,
            country_name=profile.name if profile else code,
            country_name_en=profile.name_en if profile else code,
            region=profile.region if profile else None,
            confidence="manual",
            source="manual",
            signals=[GeoSignal(code, "manual", 0.0, "указано менеджером (--geo)")],
        )

    scores: dict[str, float] = defaultdict(float)
    signals: list[GeoSignal] = []

    # 1. язык контента: учитываем все языки с долей от 15%
    for lang, share in language.distribution.items():
        if share < 0.15:
            continue
        for code, profile in profiles.items():
            weight = profile.languages.get(lang)
            if not weight:
                continue
            value = LANGUAGE_WEIGHT * weight * share
            scores[code] += value
            signals.append(
                GeoSignal(code, "language", value,
                          f"язык: {language_name(lang)} ({share:.0%} постов)")
            )

    # 2. страна в профиле
    for country in profile_countries or []:
        code = country.upper()
        if code in profiles or len(code) == 2:
            scores[code] += PROFILE_WEIGHT
            signals.append(GeoSignal(code, "profile", PROFILE_WEIGHT, "страна указана в профиле"))

    # 3-4. местные реалии
    for code, profile in profiles.items():
        hits = find_markers(texts, profile.markers)
        if not hits:
            continue
        posts_with_hits = sum(
            1 for text in texts
            if text and any(_marker_regex(m).search(text) for m in hits)
        )
        value = min(
            MARKER_CAP,
            MARKER_DISTINCT_WEIGHT * len(hits) + MARKER_POST_WEIGHT * min(posts_with_hits, 5),
        )
        scores[code] += value
        shown = ", ".join(_display_marker(m) for m in list(hits)[:5])
        signals.append(
            GeoSignal(code, "markers", value,
                      f"местные маркеры: {shown} (в {posts_with_hits} текстах)")
        )

    # 5. подсказка LLM
    if llm_country and len(llm_country) == 2:
        code = llm_country.upper()
        scores[code] += LLM_WEIGHT
        detail = "оценка LLM"
        if llm_evidence:
            detail += f": {llm_evidence[0][:120]}"
        signals.append(GeoSignal(code, "llm", LLM_WEIGHT, detail))

    if not scores:
        return GeoEstimate(confidence="low", signals=signals)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    if top < MIN_SCORE:
        return GeoEstimate(confidence="low", signals=signals,
                           alternatives=[(code, round(value, 2)) for code, value in ranked[:3]])
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    best_signals = [s for s in signals if s.country == best]
    kinds = {s.kind for s in best_signals}
    marker_strength = sum(s.weight for s in best_signals if s.kind == "markers")

    if top >= 6 and top >= 2 * second and (
        "profile" in kinds or marker_strength >= 3.5 or len(kinds) >= 3
    ):
        confidence = "high"
    elif top >= 3 and top >= 1.5 * second:
        confidence = "medium"
    else:
        confidence = "low"

    profile = profiles.get(best)
    return GeoEstimate(
        country=best,
        country_name=profile.name if profile else best,
        country_name_en=profile.name_en if profile else best,
        region=profile.region if profile else None,
        confidence=confidence,
        source="auto",
        signals=sorted(signals, key=lambda s: (s.country != best, -s.weight)),
        alternatives=[(code, round(value, 2)) for code, value in ranked[1:4]],
    )
