"""Черновик заявки на повышение ставки (раздел 9 ТЗ).

Шаблон `templates/whitelist_request.md`:
  {{ПОЛЕ}}          — подставляется кодом локально (метрики, тир, CPA, сделка);
  [[СЛОТ: задание]] — текст пишет LLM по публичным данным.

Так внутренние пороги и ставки не попадают в LLM-запрос (раздел 11.6), а текст
всё равно получается связным. Без LLM слоты заполняются простым локальным текстом.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .flags import EXPLANATIONS, EXPLANATIONS_EN
from .llm import LLMClient, LLMError, write_draft_slots
from .models import (
    INSUFFICIENT,
    AudienceAssessment,
    DealProposal,
    Flag,
    GeoEstimate,
    PlatformData,
    PlatformMetrics,
    QualificationResult,
    platform_key,
)
from .qualify import platform_label

_COMMENT_RE = re.compile(r"<!--.*?-->\n?", re.S)
_SLOT_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)(?::\s*(.*?))?\]\]", re.S)
_FIELD_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")

T = {
    "en": {
        "individual": "individual", "institutional": "institutional",
        "subscribers": "subscribers", "followers": "followers", "members": "members",
        "median_views": "median views", "posts_30d": "posts in the last 30 days",
        "videos_30d": "videos in the last 30 days",
        "avg_reactions": "avg reactions", "chat": "discussion chat", "yes": "yes", "no": "no",
        "sells_ads": "sells ads", "of_posts": "of the last {n} posts",
        "unavailable": "data unavailable",
        "audience": "Audience type: {type}; conversion into trading volume: {conv}.",
        "audience_na": "Audience assessment: not available (LLM layer was not run).",
        "geo": "Estimated geo: {name} ({conf} confidence{confirm}).",
        "geo_manual": "Geo: {name} (confirmed by the manager).",
        "geo_unknown": "Geo: not determined — to be confirmed with the partner.",
        "confirm": ", to be confirmed with the partner",
        "tier": "Requested rate: {rate}% ({atype}).",
        "tier_none": "The partner does not meet the minimum tier ({rate}%) yet.",
        "criteria_met": "Criteria: {have} {metric} — meets the {rate}% tier threshold ({need}).",
        "criteria_gap": "{missing} {metric} short of the {rate}% tier ({need}).",
        "metric_followers": "followers on a single platform",
        "metric_community": "community members",
        "cpa": "CPA for {geo}: {value} {cur} per {event}.",
        "cpa_none": "CPA for {geo}: no rate in the table.",
        "deal_hold": "Hold the rate increase until manual verification ({flags}). "
                     "If verified: {rate}% for a {days}-day test period, no CPA and no fixed fee.",
        "deal_base": "Standard program terms; no rate increase at this point.",
        "deal_test": "{rate}% revenue share for a {days}-day test period, no CPA and no fixed fee.",
        "deal_cpa": "{rate}% revenue share; CPA ({cpa}) as an option after {days} days "
                    "if the share of trading users is confirmed.",
        "review": "Review after {days} days based on: {metric}.",
        "notes": "**Manager notes**",
        "risks_none": "- No automatic risk flags; geo and audience quality are still to be "
                      "confirmed with the partner.",
        "manual": "[to be filled in manually]",
        "summary": "Mostly {type}{secondary}. Conversion forecast: {conv}{reason}.",
        "with": " with some {secondary}",
        "just_followers": "- {n} {word} on {platform}",
        "just_views": "- Median views {views} (ER {er}%)",
        "just_active": "- Active: {n} posts in the last 30 days",
        "just_chat": "- Live community: {title} with {n} members",
        "just_chat_plain": "- Discussion chat is open",
        "just_reactions": "- Avg reactions per post: {n}",
        "just_none": "- To be filled in by the manager.",
        "conv": {"high": "high", "medium": "medium", "low": "low", INSUFFICIENT: "insufficient data"},
        "types": {
            "traders": "traders", "long_term_investors": "long-term investors",
            "airdrop_hunters": "airdrop hunters", "beginners": "beginners",
            "mixed": "mixed audience", "off_target": "off-target audience",
            INSUFFICIENT: "insufficient data",
        },
        "conf": {"high": "high", "medium": "medium", "low": "low"},
    },
    "ru": {
        "individual": "частный партнёр", "institutional": "институциональный партнёр",
        "subscribers": "подписчиков", "followers": "фолловеров", "members": "участников",
        "median_views": "медиана просмотров", "posts_30d": "постов за 30 дней",
        "videos_30d": "видео за 30 дней",
        "avg_reactions": "среднее реакций", "chat": "чат обсуждений", "yes": "есть", "no": "нет",
        "sells_ads": "продаёт рекламу", "of_posts": "из последних {n} постов",
        "unavailable": "данные недоступны",
        "audience": "Тип аудитории: {type}; конверсия в торговый объём: {conv}.",
        "audience_na": "Оценка аудитории недоступна (LLM-слой не запускался).",
        "geo": "Предполагаемое гео: {name} (уверенность {conf}{confirm}).",
        "geo_manual": "Гео: {name} (подтверждено менеджером).",
        "geo_unknown": "Гео не определено — подтвердить у партнёра.",
        "confirm": ", подтвердить у партнёра",
        "tier": "Запрашиваемая ставка: {rate}% ({atype}).",
        "tier_none": "Партнёр пока не проходит минимальный тир ({rate}%).",
        "criteria_met": "Критерии: {have} {metric} — порог тира {rate}% ({need}) выполнен.",
        "criteria_gap": "До тира {rate}% ({need}) не хватает {missing} {metric}.",
        "metric_followers": "подписчиков на одной площадке",
        "metric_community": "участников комьюнити",
        "cpa": "CPA по гео {geo}: {value} {cur} за {event}.",
        "cpa_none": "CPA по гео {geo}: ставки в таблице нет.",
        "deal_hold": "Повышение отложить до ручной проверки ({flags}). Если проверка пройдена: "
                     "{rate}% на тестовый период {days} дней, без CPA и фикса.",
        "deal_base": "Базовые условия программы, без повышения ставки.",
        "deal_test": "{rate}% revenue share на тестовый период {days} дней, без CPA и фикса.",
        "deal_cpa": "{rate}% revenue share; CPA ({cpa}) как опция после {days} дней, "
                    "если доля торгующих подтвердится.",
        "review": "Пересмотр через {days} дней по: {metric}.",
        "notes": "**Заметки менеджера**",
        "risks_none": "- Автоматических флагов риска нет; гео и качество аудитории "
                      "подтвердить у партнёра.",
        "manual": "[заполнить вручную]",
        "summary": "В основном {type}{secondary}. Прогноз конверсии: {conv}{reason}.",
        "with": " с частью {secondary}",
        "just_followers": "- {n} {word} в {platform}",
        "just_views": "- Медиана просмотров {views} (ER {er}%)",
        "just_active": "- Активен: {n} постов за 30 дней",
        "just_chat": "- Живое комьюнити: {title}, {n} участников",
        "just_chat_plain": "- Открыт чат обсуждений",
        "just_reactions": "- Среднее реакций на пост: {n}",
        "just_none": "- Заполнить менеджеру.",
        "conv": {"high": "высокий", "medium": "средний", "low": "низкий", INSUFFICIENT: "недостаточно данных"},
        "types": {
            "traders": "трейдеры", "long_term_investors": "инвесторы-долгосрочники",
            "airdrop_hunters": "айрдроп-охотники", "beginners": "новички",
            "mixed": "смешанная аудитория", "off_target": "нецелевая аудитория",
            INSUFFICIENT: "недостаточно данных",
        },
        "conf": {"high": "высокая", "medium": "средняя", "low": "низкая"},
    },
}


def _t(lang: str) -> dict:
    return T.get(lang, T["en"])


def num(value: Optional[float], lang: str = "ru") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer() and abs(value) < 100:
        return f"{value:.1f}"
    text = f"{int(round(value)):,}"
    return text.replace(",", " ") if lang == "ru" else text


def parse_template(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Убирает комментарии. -> (тело, [(слот, задание)])."""
    body = _COMMENT_RE.sub("", text).lstrip("\n")
    slots: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, hint in _SLOT_RE.findall(body):
        if name not in seen:
            seen.add(name)
            slots.append((name, " ".join((hint or "").split())))
    return body, slots


def metrics_lines(
    platforms: list[PlatformData], metrics: dict[str, PlatformMetrics], lang: str
) -> list[str]:
    t = _t(lang)
    lines: list[str] = []
    for data in platforms:
        label = f"{platform_label(data)} {data.display_handle}"
        if not data.ok:
            lines.append(f"- {label}: {t['unavailable']}")
            continue
        m = metrics.get(platform_key(data))
        word = t["followers"] if data.platform == "x" else (
            t["members"] if data.kind == "group" else t["subscribers"])
        parts = [f"{num(data.followers, lang)} {word}"]
        if m is not None:
            if m.median_views is not None:
                er = f" (ER {m.er}%)" if m.er is not None else ""
                parts.append(f"{t['median_views']} {num(m.median_views, lang)}{er}")
            unit = t["videos_30d"] if data.platform == "youtube" else t["posts_30d"]
            parts.append(f"{m.posts_30d}{'+' if m.posts_30d_capped else ''} {unit}")
            if m.avg_reactions is not None:
                parts.append(f"{t['avg_reactions']} {num(m.avg_reactions, lang)}")
            if m.has_community_chat is not None and data.kind != "group":
                parts.append(f"{t['chat']}: {t['yes'] if m.has_community_chat else t['no']}")
            if m.ads_window:
                ads = t["yes"] if m.sells_ads else t["no"]
                parts.append(f"{t['sells_ads']}: {ads} ({m.ads_posts} {t['of_posts'].format(n=m.ads_window)})")
        lines.append(f"- {label}: " + "; ".join(parts))
    return lines


def audience_line(audience: AudienceAssessment, lang: str) -> str:
    t = _t(lang)
    if not audience.available:
        return t["audience_na"]
    return t["audience"].format(
        type=t["types"].get(audience.audience_type.value, audience.audience_type.value),
        conv=t["conv"].get(audience.conversion.value, audience.conversion.value),
    )


def geo_line(geo: GeoEstimate, lang: str) -> str:
    t = _t(lang)
    name = geo.country_name_en if lang == "en" else geo.country_name
    if not geo.country:
        return t["geo_unknown"]
    if geo.source == "manual":
        return t["geo_manual"].format(name=name)
    return t["geo"].format(
        name=name,
        conf=t["conf"].get(geo.confidence, geo.confidence),
        confirm=t["confirm"] if geo.needs_confirmation else "",
    )


def local_fields(
    *,
    partner_name: str,
    platforms: list[PlatformData],
    metrics: dict[str, PlatformMetrics],
    audience: AudienceAssessment,
    geo: GeoEstimate,
    qual: QualificationResult,
    deal: DealProposal,
    flags: list[Flag],
    notes: str,
    lang: str,
    now: datetime,
) -> dict[str, str]:
    """Значения {{ПОЛЕЙ}} — всё, что считается по внутренним данным, только здесь."""
    t = _t(lang)
    metric_names = {
        "followers_single_platform": t["metric_followers"],
        "community_members": t["metric_community"],
    }

    if qual.tier_rate is not None:
        tier_line = t["tier"].format(rate=f"{qual.tier_rate:g}", atype=t.get(qual.affiliate_type, qual.affiliate_type))
    else:
        tier_line = t["tier_none"].format(rate=f"{qual.lowest_tier_rate:g}" if qual.lowest_tier_rate else "—")

    criteria: list[str] = []
    values = {
        "followers_single_platform": qual.followers_single_platform,
        "community_members": qual.community_members,
    }
    if qual.tier_rate is not None and qual.tier_met_by:
        metric = qual.tier_met_by[0]
        criteria.append(t["criteria_met"].format(
            have=num(values[metric], lang), metric=metric_names[metric],
            rate=f"{qual.tier_rate:g}", need=num(qual.tier_thresholds.get(metric), lang),
        ))
    for gap in qual.next_tier_gaps[:2]:
        criteria.append(t["criteria_gap"].format(
            missing=num(gap.missing, lang), metric=metric_names[gap.metric],
            rate=f"{qual.next_tier_rate:g}", need=num(gap.need, lang),
        ))

    geo_label = geo.country or "—"
    if qual.cpa is not None:
        cpa_line = t["cpa"].format(geo=geo_label, value=f"{qual.cpa:g}", cur=qual.cpa_currency, event=qual.cpa_event)
    else:
        cpa_line = t["cpa_none"].format(geo=geo_label)

    critical = sorted({f.code for f in flags if f.severity == "critical"})
    rate = f"{deal.rate:g}" if deal.rate is not None else "—"
    if deal.rate is None:
        deal_text = t["deal_base"]
    elif not deal.submit:
        deal_text = t["deal_hold"].format(flags=", ".join(critical) or "—", rate=rate, days=deal.test_period_days)
    elif deal.cpa_included and qual.cpa is not None:
        deal_text = t["deal_cpa"].format(
            rate=rate, cpa=f"{qual.cpa:g} {qual.cpa_currency} / {qual.cpa_event}", days=deal.test_period_days)
    else:
        deal_text = t["deal_test"].format(rate=rate, days=deal.test_period_days)

    explanations = EXPLANATIONS_EN if lang == "en" else EXPLANATIONS
    risks = [f"- {f.code}: {explanations.get(f.code, f.explanation)}" for f in flags]

    return {
        "PARTNER_NAME": partner_name,
        "PARTNER_TYPE": t.get(qual.affiliate_type, qual.affiliate_type),
        "LINKS": ", ".join(d.url for d in platforms),
        "CHECK_DATE": now.date().isoformat(),
        "METRICS": "\n".join(metrics_lines(platforms, metrics, lang)),
        "AUDIENCE_LINE": audience_line(audience, lang),
        "GEO_LINE": geo_line(geo, lang),
        "TIER_LINE": tier_line,
        "CRITERIA_LINE": " ".join(criteria),
        "CPA_LINE": cpa_line,
        "DEAL_STRUCTURE": deal_text,
        "REVIEW_TERMS": t["review"].format(days=deal.test_period_days, metric=deal.review_metric),
        "RISKS_LIST": "\n".join(risks) or t["risks_none"],
        "MANAGER_NOTES": f"\n{t['notes']}\n{notes.strip()}" if notes and notes.strip() else "",
    }


def public_context(
    *,
    partner_name: str,
    platforms: list[PlatformData],
    metrics: dict[str, PlatformMetrics],
    audience: AudienceAssessment,
    geo: GeoEstimate,
    flags: list[Flag],
) -> str:
    """Факты для LLM: только публичные данные, без тиров, порогов и ставок."""
    lines = [f"Partner: {partner_name}", "Platforms and public metrics:"]
    lines += metrics_lines(platforms, metrics, "en")
    lines.append(geo_line(geo, "en"))
    if audience.available:
        def fmt(label: str, assessed) -> None:
            evidence = "; ".join(assessed.evidence[:3])
            lines.append(f"{label}: {assessed.value}" + (f" — evidence: {evidence}" if evidence else ""))

        fmt("Audience type", audience.audience_type)
        if audience.audience_secondary not in ("none", INSUFFICIENT):
            lines.append(f"Secondary audience: {audience.audience_secondary}")
        fmt("Conversion forecast", audience.conversion)
        if audience.conversion_reason:
            lines.append(f"Conversion reason (ru): {audience.conversion_reason}")
        fmt("Topics", audience.topics)
        fmt("Tone", audience.tone)
        lines.append(f"Brand fit: {audience.brand_fit}")
        fmt("Airdrop content share", audience.airdrop_share)
        for flag in audience.red_flags:
            lines.append(f"Content red flag ({flag.type}): «{flag.quote}» — {flag.comment}")
    else:
        lines.append("Audience assessment: not available")
    if flags:
        lines.append("Risk flags:")
        for flag in flags:
            lines.append(f"- {flag.code}: {EXPLANATIONS_EN.get(flag.code, flag.code)}")
    else:
        lines.append("Risk flags: none detected automatically")
    return "\n".join(lines)


def local_slots(
    slots: list[tuple[str, str]],
    *,
    platforms: list[PlatformData],
    metrics: dict[str, PlatformMetrics],
    audience: AudienceAssessment,
    flags: list[Flag],
    lang: str,
) -> dict[str, str]:
    """Простой текст слотов без LLM — чтобы черновик был всегда."""
    t = _t(lang)
    values: dict[str, str] = {}
    for name, _hint in slots:
        if name == "AUDIENCE_SUMMARY":
            if audience.available and audience.audience_type.known:
                secondary = ""
                if audience.audience_secondary not in ("none", INSUFFICIENT):
                    secondary = t["with"].format(
                        secondary=t["types"].get(audience.audience_secondary, audience.audience_secondary))
                reason = f" — {audience.conversion_reason}" if audience.conversion_reason and lang == "ru" else ""
                values[name] = t["summary"].format(
                    type=t["types"].get(audience.audience_type.value, audience.audience_type.value),
                    secondary=secondary,
                    conv=t["conv"].get(audience.conversion.value, audience.conversion.value),
                    reason=reason,
                )
            else:
                values[name] = t["manual"]
        elif name == "JUSTIFICATION":
            values[name] = _justification(platforms, metrics, lang)
        elif name == "RISKS":
            explanations = EXPLANATIONS_EN if lang == "en" else EXPLANATIONS
            values[name] = "\n".join(
                f"- {f.code}: {explanations.get(f.code, f.explanation)}" for f in flags
            ) or t["risks_none"]
        else:
            values[name] = t["manual"]
    return values


def _justification(platforms: list[PlatformData], metrics: dict[str, PlatformMetrics], lang: str) -> str:
    t = _t(lang)
    lines: list[str] = []
    for data in platforms:
        if not data.ok or not data.followers:
            continue
        m = metrics.get(platform_key(data))
        word = t["members"] if data.kind == "group" else (t["followers"] if data.platform == "x" else t["subscribers"])
        lines.append(t["just_followers"].format(n=num(data.followers, lang), word=word, platform=platform_label(data)))
        if m is None:
            continue
        if m.median_views is not None and m.er is not None:
            lines.append(t["just_views"].format(views=num(m.median_views, lang), er=m.er))
        if m.posts_30d:
            lines.append(t["just_active"].format(n=f"{m.posts_30d}{'+' if m.posts_30d_capped else ''}"))
        if data.community_members and data.kind != "group":
            lines.append(t["just_chat"].format(title=data.community_title or "chat", n=num(data.community_members, lang)))
        elif m.has_community_chat:
            lines.append(t["just_chat_plain"])
        if m.avg_reactions:
            lines.append(t["just_reactions"].format(n=num(m.avg_reactions, lang)))
    return "\n".join(lines) or t["just_none"]


def render(body: str, fields: dict[str, str], slots: dict[str, str]) -> str:
    text = _SLOT_RE.sub(lambda m: slots.get(m.group(1), m.group(0)), body)
    text = _FIELD_RE.sub(lambda m: fields.get(m.group(1), m.group(0)), text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def build_draft(
    template: str,
    *,
    llm: Optional[LLMClient],
    partner_name: str,
    platforms: list[PlatformData],
    metrics: dict[str, PlatformMetrics],
    audience: AudienceAssessment,
    geo: GeoEstimate,
    qual: QualificationResult,
    deal: DealProposal,
    flags: list[Flag],
    notes: str,
    lang: str,
    now: datetime,
) -> tuple[str, str, Optional[str]]:
    """-> (текст, источник слотов llm|local, ошибка LLM или None)."""
    body, slots = parse_template(template)
    fields = local_fields(
        partner_name=partner_name, platforms=platforms, metrics=metrics, audience=audience,
        geo=geo, qual=qual, deal=deal, flags=flags, notes=notes, lang=lang, now=now,
    )
    slot_values = local_slots(
        slots, platforms=platforms, metrics=metrics, audience=audience, flags=flags, lang=lang,
    )
    source, error = "local", None
    if slots and llm is not None and llm.available:
        context = public_context(
            partner_name=partner_name, platforms=platforms, metrics=metrics,
            audience=audience, geo=geo, flags=flags,
        )
        try:
            generated, _cached = write_draft_slots(llm, slots, context, lang)
            for name, value in generated.items():
                if value:
                    slot_values[name] = value
            source = "llm"
        except LLMError as exc:
            error = str(exc)
    return render(body, fields, slot_values), source, error
