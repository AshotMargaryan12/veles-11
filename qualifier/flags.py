"""Флаги накрутки и риска (раздел 6 ТЗ).

Каждый флаг — код, что он значит для сделки и на чём основан. Метрические флаги
считаются по каждой площадке с данными; airdrop_heavy и brand_risk берутся из
LLM-оценки, а без LLM — из словарей thresholds.yaml.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from .config import Thresholds
from .models import (
    KIND_GROUP,
    PLATFORM_TELEGRAM,
    PLATFORM_TITLES,
    AudienceAssessment,
    Flag,
    PlatformData,
    PlatformMetrics,
    platform_key,
)

EXPLANATIONS = {
    "low_er": "охват ниже нормы для такого размера — реальная аудитория меньше числа "
              "подписчиков, ставка по подписчикам будет завышена",
    "views_spike": "просмотры отдельных постов аномально выше медианы — возможна закупка "
                   "просмотров; судить по медиане, а не по лучшим постам",
    "dead_subs": "много подписчиков при низких просмотрах и почти без реакций — база, "
                 "вероятно, накручена или «мёртвая»; регистраций будет меньше ожидаемого",
    "no_comments": "комьюнити закрыто и реакций почти нет — живость аудитории не "
                   "подтверждается; запросить у партнёра статистику",
    "airdrop_heavy": "аудитория даёт регистрации, но слабо конвертируется в объём",
    "inactive": "давно нет постов — интеграция не даст охвата, уточнить планы партнёра",
    "brand_risk": "контент несёт репутационный риск для бренда (обещания доходности, "
                  "пампы, сомнительные проекты)",
}

# Те же расшифровки для черновика заявки на английском
EXPLANATIONS_EN = {
    "low_er": "reach is below the norm for this audience size — the real audience is smaller "
              "than the subscriber count",
    "views_spike": "some posts get abnormally more views than the median — possible bought "
                   "views; reach should be judged by the median",
    "dead_subs": "many subscribers with low views and almost no reactions — the subscriber "
                 "base may be inflated or inactive",
    "no_comments": "comments are closed and there are almost no reactions — audience "
                   "engagement cannot be confirmed from public data",
    "airdrop_heavy": "airdrop-focused audience: brings sign-ups but converts weakly into "
                     "trading volume",
    "inactive": "no recent posts — an integration may not reach the audience",
    "brand_risk": "content carries a reputational risk for the brand (return promises, "
                  "pumps, dubious projects)",
}

# Красные флаги контента, при которых повышение не подаём без ручной проверки.
# Обещание «заработка» в описании или рефки на другие биржи — риск, но не стоп.
SEVERE_RED_FLAGS = {"guaranteed_returns", "pump", "dubious_projects"}

RED_FLAG_LABELS = {
    "guaranteed_returns": "обещание гарантированной доходности",
    "get_rich_quick": "схема быстрого заработка",
    "pump": "памп-активность",
    "dubious_projects": "сомнительные проекты",
    "competitor_referrals": "агрессивные рефки на другие биржи",
    "other": "прочее",
}


@lru_cache(maxsize=1024)
def _keyword_re(keyword: str) -> re.Pattern[str]:
    if re.fullmatch(r"[A-Za-z0-9 \-]+", keyword):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(keyword), re.IGNORECASE)
    return re.compile(re.escape(keyword), re.IGNORECASE)


def keyword_share(texts: list[str], keywords: list[str]) -> tuple[float, int]:
    """Доля текстов, где есть хотя бы одно ключевое слово. -> (доля, сколько)."""
    texts = [t for t in texts if t and t.strip()]
    if not texts or not keywords:
        return 0.0, 0
    patterns = [_keyword_re(k) for k in keywords if k]
    hits = sum(1 for t in texts if any(p.search(t) for p in patterns))
    return hits / len(texts), hits


def keyword_quotes(texts: list[str], keywords: list[str], limit: int = 2) -> list[str]:
    """Фрагменты текста вокруг найденных слов — как основание флага."""
    quotes: list[str] = []
    for text in texts:
        for keyword in keywords:
            match = _keyword_re(keyword).search(text or "")
            if not match:
                continue
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 60)
            fragment = " ".join(text[start:end].split())
            quotes.append(f"«{fragment}»")
            break
        if len(quotes) >= limit:
            break
    return quotes


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _label(platform: str) -> str:
    return PLATFORM_TITLES.get(platform, platform)


def metric_flags(data: PlatformData, m: PlatformMetrics, th: Thresholds) -> list[Flag]:
    flags: list[Flag] = []
    if not data.ok:
        return flags
    p = data.platform
    label = _label(p)

    inactive_days = int(th["inactive"]["max_days_since_last_post"])
    if m.days_since_last_post is not None and m.days_since_last_post > inactive_days:
        flags.append(Flag(
            "inactive", EXPLANATIONS["inactive"],
            f"{label}: последний пост {m.days_since_last_post} дн. назад (порог {inactive_days})", p,
        ))
    elif m.items_total == 0 and data.kind != KIND_GROUP:
        flags.append(Flag("inactive", EXPLANATIONS["inactive"], f"{label}: постов нет", p))

    if data.kind == KIND_GROUP or not data.followers:
        return flags

    if m.er is not None and m.views_sample >= 3:
        min_er = th.min_er(p, data.followers)
        if min_er is not None and m.er < min_er:
            flags.append(Flag(
                "low_er", EXPLANATIONS["low_er"],
                f"{label}: ER {m.er}% при пороге {min_er}% для {_n(data.followers)} подписчиков",
                p,
            ))

    spike = th["views_spike"]
    if m.views_sample >= int(spike["min_sample"]):
        reasons = []
        if m.views_cv is not None and m.views_cv > float(spike["cv_max"]):
            reasons.append(f"разброс просмотров {m.views_cv:.2f} (порог {spike['cv_max']})")
        if m.max_to_median is not None and m.max_to_median > float(spike["max_to_median"]):
            reasons.append(f"лучший пост в {m.max_to_median:.1f} раза выше медианы (порог {spike['max_to_median']})")
        if reasons:
            flags.append(Flag("views_spike", EXPLANATIONS["views_spike"], f"{label}: " + "; ".join(reasons), p))

    dead = th["dead_subs"]
    dead_max_er = th.per_platform(dead.get("max_er"), p)
    # реакции: в Telegram None = реакции отключены (обратной связи нет вовсе);
    # на YouTube/X None = источник их не отдал, судить нельзя
    reactions_low = (
        (m.avg_reactions is None and p == PLATFORM_TELEGRAM)
        or (m.avg_reactions is not None and m.avg_reactions <= float(dead["max_avg_reactions"]))
    )
    if (
        dead_max_er is not None
        and data.followers >= int(dead["min_followers"])
        and m.er is not None
        and m.er < dead_max_er
        and reactions_low
    ):
        reactions = "реакции отключены" if m.avg_reactions is None else f"реакций в среднем {m.avg_reactions}"
        flags.append(Flag(
            "dead_subs", EXPLANATIONS["dead_subs"],
            f"{label}: {_n(data.followers)} подписчиков, ER {m.er}%, {reactions}",
            p, severity="critical",
        ))

    if p == PLATFORM_TELEGRAM and data.has_community_chat is False:
        max_reactions = float(th["no_comments"]["max_avg_reactions"])
        if m.avg_reactions is None or m.avg_reactions <= max_reactions:
            reactions = "реакции отключены" if m.avg_reactions is None else f"реакций в среднем {m.avg_reactions}"
            flags.append(Flag(
                "no_comments", EXPLANATIONS["no_comments"],
                f"{label}: чата обсуждений нет, {reactions}", p,
            ))
    return flags


def content_flags(
    platforms: list[PlatformData],
    audience: AudienceAssessment,
    th: Thresholds,
) -> list[Flag]:
    flags: list[Flag] = []
    texts: list[str] = []
    for data in platforms:
        if data.ok:
            texts.append(data.description)
            texts.extend(i.text for i in data.items)

    # --- airdrop_heavy ---------------------------------------------------
    llm_airdrop = audience.available and (
        audience.audience_type.value == "airdrop_hunters"
        or audience.airdrop_share.value == "dominant"
    )
    if llm_airdrop:
        source = audience.airdrop_share if audience.airdrop_share.value == "dominant" else audience.audience_type
        evidence = source.evidence[0] if source.evidence else "по оценке LLM"
        flags.append(Flag("airdrop_heavy", EXPLANATIONS["airdrop_heavy"], evidence, source="llm"))
    elif not (audience.available and audience.airdrop_share.known):
        cfg = th["airdrop"]
        posts = [t for d in platforms if d.ok for t in (i.text for i in d.items)]
        share, hits = keyword_share(posts, list(cfg.get("keywords") or []))
        if posts and share >= float(cfg["min_share"]):
            flags.append(Flag(
                "airdrop_heavy", EXPLANATIONS["airdrop_heavy"],
                f"айрдроп-лексика в {hits} из {len([p for p in posts if p.strip()])} постов ({share:.0%})",
                source="keywords",
            ))

    # --- brand_risk ------------------------------------------------------
    if audience.available and audience.red_flags:
        parts = []
        for flag in audience.red_flags[:3]:
            mark = "" if flag.verified else " (цитата не найдена в постах — проверить)"
            parts.append(f"{RED_FLAG_LABELS.get(flag.type, flag.type)}: «{flag.quote[:160]}»{mark}")
        severe = any(f.verified and f.type in SEVERE_RED_FLAGS for f in audience.red_flags)
        flags.append(Flag(
            "brand_risk", EXPLANATIONS["brand_risk"], "; ".join(parts),
            severity="critical" if severe else "warn", source="llm",
        ))
    else:
        markers = list(th.get("brand_risk_markers") or [])
        share, hits = keyword_share(texts, markers)
        if hits:
            quotes = keyword_quotes(texts, markers)
            # словарь не понимает контекста — это повод посмотреть, а не стоп
            flags.append(Flag(
                "brand_risk", EXPLANATIONS["brand_risk"],
                f"стоп-фразы в {hits} текстах: " + "; ".join(quotes),
                source="keywords",
            ))
    return flags


def compute_flags(
    platforms: list[PlatformData],
    metrics: dict[str, PlatformMetrics],
    audience: AudienceAssessment,
    th: Thresholds,
) -> list[Flag]:
    flags: list[Flag] = []
    for data in platforms:
        m: Optional[PlatformMetrics] = metrics.get(platform_key(data))
        if m is not None:
            flags.extend(metric_flags(data, m, th))
    flags.extend(content_flags(platforms, audience, th))
    return flags
