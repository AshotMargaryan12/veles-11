"""Квалификация по внутренним критериям (раздел 7 ТЗ) и рекомендация.

Считается только здесь, локально. Пороги тиров и CPA-ставки в LLM не уходят.
"""

from __future__ import annotations

from typing import Optional

from .config import AffiliateType, ConfigError, Criteria, CpaTable, Tier
from .models import (
    KIND_GROUP,
    PerformanceResult,
    PLATFORM_TITLES,
    PLATFORM_TELEGRAM,
    AudienceAssessment,
    DealProposal,
    Flag,
    GeoEstimate,
    PlatformData,
    QualificationResult,
    TierGap,
)

METRIC_LABELS = {
    "followers_single_platform": "подписчиков на одной площадке",
    "community_members": "участников комьюнити",
    "followers_with_views": "средних просмотров поста",
}


def community_size(platforms: list[PlatformData]) -> tuple[int, str]:
    """Крупнейшее комьюнити: Telegram-группа партнёра или чат обсуждений канала."""
    best, source = 0, ""
    for data in platforms:
        if not data.ok or not data.community_members:
            continue
        if data.community_members > best:
            best = data.community_members
            kind = "чат" if data.kind == KIND_GROUP else "чат обсуждений"
            title = data.community_title or data.title
            source = f"{kind} «{title}»" if title else kind
    return best, source


def largest_platform(platforms: list[PlatformData]) -> tuple[int, Optional[str]]:
    best, platform = 0, None
    for data in platforms:
        if data.ok and data.kind != KIND_GROUP and (data.followers or 0) > best:
            best, platform = int(data.followers or 0), data.platform
    return best, platform


def views_by_platform(platforms: list[PlatformData], metrics: Optional[dict] = None) -> list[tuple[int, int]]:
    """(подписчики, средние просмотры поста) по каждой площадке-каналу."""
    from .models import platform_key

    pairs = []
    for data in platforms:
        if not data.ok or data.kind == KIND_GROUP or not data.followers:
            continue
        m = (metrics or {}).get(platform_key(data))
        if m is not None and m.avg_views is not None:
            pairs.append((int(data.followers), int(m.avg_views)))
    return pairs


def _combo_met(tier: Tier, pairs: list[tuple[int, int]]) -> bool:
    if not tier.views_combo:
        return False
    followers, views = tier.views_combo
    return any(f >= followers and v >= views for f, v in pairs)


def _tier_met(tier: Tier, values: dict[str, int], match: str,
              pairs: Optional[list[tuple[int, int]]] = None) -> list[str]:
    met = [k for k, need in tier.conditions.items() if values.get(k, 0) >= need]
    total = len(tier.conditions)
    if tier.views_combo:
        total += 1
        if _combo_met(tier, pairs or []):
            met.append("followers_with_views")
    if match == "all":
        return met if len(met) == total else []
    return met


def qualify(
    platforms: list[PlatformData],
    criteria: Criteria,
    cpa: CpaTable,
    geo: GeoEstimate,
    affiliate_type: str = "individual",
    metrics: Optional[dict] = None,
) -> QualificationResult:
    if affiliate_type not in criteria.affiliate_types:
        raise ConfigError(
            f"тип партнёра «{affiliate_type}» не описан в criteria.yaml. "
            f"Доступны: {', '.join(criteria.affiliate_types)}"
        )
    atype: AffiliateType = criteria.affiliate_types[affiliate_type]

    followers, followers_platform = largest_platform(platforms)
    community, community_source = community_size(platforms)
    values = {"followers_single_platform": followers, "community_members": community}

    result = QualificationResult(
        affiliate_type=affiliate_type,
        followers_single_platform=followers,
        followers_platform=followers_platform,
        community_members=community,
        community_source=community_source,
        lowest_tier_rate=atype.tiers[-1].rate,
        criteria_are_examples=criteria.is_example,
    )

    current_index: Optional[int] = None
    pairs = views_by_platform(platforms, metrics)
    for index, tier in enumerate(atype.tiers):  # от высшей ставки к низшей
        met = _tier_met(tier, values, atype.match, pairs)
        if met:
            current_index = index
            result.tier_rate = tier.rate
            result.tier_met_by = met
            result.tier_thresholds = dict(tier.conditions)
            if tier.views_combo:
                result.tier_thresholds["followers_with_views"] = tier.views_combo[0]
                result.tier_thresholds["avg_views"] = tier.views_combo[1]
            break

    next_tier: Optional[Tier] = None
    if current_index is None:
        next_tier = atype.tiers[-1]
    elif current_index > 0:
        next_tier = atype.tiers[current_index - 1]
    if next_tier is not None:
        result.next_tier_rate = next_tier.rate
        result.next_tier_gaps = [
            TierGap(metric=k, have=values.get(k, 0), need=need)
            for k, need in next_tier.conditions.items()
            if values.get(k, 0) < need
        ]
        # составное условие показываем, только если подписчиков хватает, а просмотров — нет
        if next_tier.views_combo and not _combo_met(next_tier, pairs):
            need_f, need_v = next_tier.views_combo
            eligible = [v for f, v in pairs if f >= need_f]
            if eligible:
                result.next_tier_gaps.append(TierGap(
                    metric="followers_with_views", have=max(eligible), need=need_v,
                    detail=f"при {need_f:,}+ подписчиков".replace(",", " "),
                ))

    # --- «между порогами» ---------------------------------------------------
    margin = criteria.borderline_margin
    reasons: list[str] = []
    if result.next_tier_gaps and margin > 0:
        closest = min(result.next_tier_gaps, key=lambda g: g.missing / max(g.need, 1))
        if closest.missing / max(closest.need, 1) <= margin:
            reasons.append(
                f"до тира {next_tier.rate:g}% не хватает {closest.missing:,} "
                f"{METRIC_LABELS[closest.metric]} — партнёр между порогами".replace(",", " ")
            )
    if current_index is not None and margin > 0:
        tier = atype.tiers[current_index]
        for metric in result.tier_met_by:
            if metric not in tier.conditions:
                continue  # составное условие «впритык» не оцениваем
            need = tier.conditions[metric]
            if values[metric] < need * (1 + margin):
                reasons.append(
                    f"тир {tier.rate:g}% взят впритык: {values[metric]:,} при пороге {need:,} "
                    f"{METRIC_LABELS[metric]}".replace(",", " ")
                )
                break

    # --- CPA по гео ---------------------------------------------------------
    rate, matched_by = cpa.lookup(geo.country, geo.region)
    if rate is not None:
        result.cpa = rate.rate
        result.cpa_currency = rate.currency
        result.cpa_event = rate.event
        result.cpa_matched_by = matched_by
    elif geo.country:
        reasons.append(f"нет CPA-ставки для гео {geo.country} в {cpa.path.name if cpa.path else 'cpa_by_country.csv'}")

    if geo.confidence == "low":
        reasons.append("гео определено с низкой уверенностью")
    if not followers and not community:
        reasons.append("нет данных о размере аудитории ни по одной площадке")
    if criteria.is_example:
        reasons.append("критерии — примеры-заглушки: заполните config/qualifier/criteria.yaml")

    result.manual_review_reasons = reasons
    result.manual_review = bool(reasons)
    return result


# --------------------------------------------------------------------------
# Рекомендация
# --------------------------------------------------------------------------

_REJECT = (
    "Ни объёмы (критерий 1), ни соцсети (критерий 2) не проходят — заявка будет отклонена. "
    "Спецсделка (критерий 3) — только case by case и с подписанным соглашением."
)


def _fmt_rate(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:g}%"


def _volume_lines(perf: "PerformanceResult", deal: DealProposal) -> tuple[list[str], bool]:
    """Строки рекомендации по объёмам (критерий 1). -> (строки, критерий выполнен)."""
    from .performance import best_rates

    lines: list[str] = []
    spot, futures, months = best_rates(perf)
    ok = spot is not None or futures is not None
    if ok:
        deal.spot_rate, deal.futures_rate, deal.months, deal.basis = spot, futures, months, "volume"
        parts = []
        for name, market in (("Spot", perf.markets["spot"]), ("Futures", perf.markets["futures"])):
            rate = spot if market.market == "spot" else futures
            if rate is None:
                continue
            if market.needs_whitelist and rate == market.whitelist_rate:
                fallback = (f"; без него — {market.auto_rate_full:g}% по автооценке"
                            if market.auto_passed else "")
                parts.append(f"{name} {rate:g}% на {market.whitelist_months} мес (whitelisting{fallback})")
            else:
                parts.append(f"{name} {rate:g}% (автооценка)")
        source = (f" Объёмы с {perf.input.source_label} — приложить скриншоты из кабинета партнёра."
                  if perf.input.external else "")
        lines.append(f"По объёмам (критерий 1): {'; '.join(parts)}.{source}")
    else:
        lines.append("Объёмы не дотягивают до критерия 1 регламента.")
    roi = perf.roi
    if roi is not None and roi.roi is not None and roi.roi < 0:
        lines.append(
            f"ROI отрицательный ({roi.roi:.2f}): выплата партнёру больше комиссии биржи — "
            "снизить ставку или фикс."
        )
    if roi is not None and roi.competitor and roi.vs_competitor < 0:
        lines.append(
            f"Конкурент платит партнёру на ${abs(roi.vs_competitor):,.0f} в месяц больше — "
            "учесть в переговорах.".replace(",", " ")
        )
    return lines, ok


def recommend(
    qual: QualificationResult,
    flags: list[Flag],
    audience: AudienceAssessment,
    geo: GeoEstimate,
    criteria: Criteria,
    perf: Optional["PerformanceResult"] = None,
) -> tuple[list[str], DealProposal]:
    """Правила рекомендации. Решение остаётся за менеджером.

    С объёмами порядок как в регламенте: критерий 1 (объёмы) → критерий 2
    (соцсети) → иначе отказ, спецсделка — case by case.
    """
    days = criteria.test_period_days
    deal = DealProposal(
        rate=qual.tier_rate,
        test_period_days=days,
        review_metric=criteria.review_metric,
        basis="social" if qual.tier_rate is not None else "none",
    )
    lines: list[str] = []
    codes = {f.code for f in flags}
    critical = [f for f in flags if f.severity == "critical"]
    conversion = audience.conversion.value if audience.available else "insufficient_data"
    airdrop = "airdrop_heavy" in codes
    volume_lines, volume_ok = _volume_lines(perf, deal) if perf is not None else ([], False)
    if volume_ok and qual.tier_rate is not None:
        best_volume = max(r for r in (deal.spot_rate, deal.futures_rate) if r is not None)
        if qual.tier_rate > best_volume:
            # соцсети дают больше — основание заявки они, объёмы остаются справкой
            volume_lines[0] = volume_lines[0].replace(
                "По объёмам (критерий 1): ", "Объёмы (критерий 1) дают меньше, чем соцсети: ", 1)
            deal.spot_rate = deal.futures_rate = deal.months = None
            deal.basis = "social"
            volume_ok = False

    if not qual.followers_single_platform and not qual.community_members:
        if not volume_lines:
            deal.submit = False
            lines.append(
                "Данных по площадкам нет — проверьте ссылки и доступы (qualify config check). "
                "Квалифицировать партнёра по этой карточке нельзя."
            )
            return lines, deal
        deal.submit = volume_ok
        lines.extend(volume_lines)
        lines.append("Площадки не проверены — аудиторию оценить нельзя, проверьте ссылки и доступы.")
        if not volume_ok:
            lines.append(_REJECT)
        return lines, deal

    # при критических флагах первой идёт строка «не подавать», объёмы — после неё
    if not critical:
        lines.extend(volume_lines)

    if qual.tier_rate is None:
        gap = ""
        if qual.next_tier_gaps:
            g = min(qual.next_tier_gaps, key=lambda x: x.missing)
            gap = f" — до минимального тира не хватает {g.missing:,} {METRIC_LABELS[g.metric]}".replace(",", " ")
        if volume_ok:
            lines.append(
                f"По соцсетям партнёр ниже минимального тира ({_fmt_rate(qual.lowest_tier_rate)}){gap} — "
                "основание заявки только объёмы."
            )
            if critical:
                deal.submit = False
                names = ", ".join(sorted({f.code for f in critical}))
                lines.insert(0, f"Не подавать на повышение до ручной проверки ({names}).")
                lines.extend(volume_lines)
        else:
            deal.submit = False
            lines.append(
                f"Повышение ставки по критериям не обосновано: партнёр ниже минимального тира "
                f"({_fmt_rate(qual.lowest_tier_rate)}){gap}. Базовые условия программы, вернуться при росте."
            )
            if perf is not None:
                lines.append(_REJECT)
    elif critical:
        deal.submit = False
        names = ", ".join(sorted({f.code for f in critical}))
        lines.append(
            f"Не подавать на повышение до ручной проверки ({names}). Запросить у партнёра "
            "статистику аудитории и охватов (скрин из админки канала)."
        )
        lines.append(
            f"Если проверка пройдена — старт на {_fmt_rate(qual.tier_rate)} на тестовый период "
            f"{days} дней, без CPA и фикса."
        )
        lines.extend(volume_lines)
    elif volume_ok:
        lines.append(
            f"Соцсети (критерий 2): {_fmt_rate(qual.tier_rate)} — дополнительное основание к объёмам."
        )
        if airdrop or conversion == "low":
            lines.append("Аудитория слабо конвертируется в объём — смотреть на долю приступивших к торговле.")
    elif airdrop or conversion == "low":
        lines.append(
            f"Старт на {_fmt_rate(qual.tier_rate)} на тестовый период ({days} дней), без CPA и фикса. "
            "В первый месяц смотреть не на количество регистраций, а на долю приступивших к торговле."
        )
    elif conversion == "high" and not codes and geo.confidence in ("high", "manual"):
        deal.cpa_included = qual.cpa is not None
        cpa = (
            f" CPA по гео ({qual.cpa:g} {qual.cpa_currency} за {qual.cpa_event}) — как опция после "
            f"{days} дней, если доля торгующих подтвердится." if qual.cpa is not None else ""
        )
        lines.append(f"Подавать на {_fmt_rate(qual.tier_rate)}.{cpa}")
    else:
        lines.append(
            f"Подавать на {_fmt_rate(qual.tier_rate)} с пересмотром через {days} дней "
            "по доле торгующих и объёму."
        )

    if perf is not None and not volume_ok and qual.tier_rate is not None:
        lines.append(f"Основание заявки — соцсети (критерий 2): {_fmt_rate(qual.tier_rate)}.")
    if geo.needs_confirmation:
        lines.append("Перед подачей подтвердить гео у партнёра (скрин статистики аудитории).")
    if "inactive" in codes:
        lines.append("Уточнить у партнёра планы по постингу: площадка неактивна.")
    if "brand_risk" in codes and not any(f.code == "brand_risk" for f in critical):
        lines.append("Проверить контент на соответствие бренду (см. brand_risk) до публикации интеграции.")
    if "low_er" in codes or "views_spike" in codes:
        lines.append("Считать охват по медиане просмотров, а не по подписчикам.")
    if qual.manual_review and qual.tier_rate is not None and any("порог" in r for r in qual.manual_review_reasons):
        lines.append("Партнёр у границы тира — финальное решение по ставке за менеджером.")
    return lines, deal


def platform_label(data: PlatformData) -> str:
    title = PLATFORM_TITLES.get(data.platform, data.platform)
    if data.platform == PLATFORM_TELEGRAM and data.kind == KIND_GROUP:
        return f"{title} (чат)"
    return title
