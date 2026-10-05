"""Карточка квалификации: markdown (консоль и файл), JSON, строка CSV (раздел 8 ТЗ)."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Iterable, Optional

from .draft import num
from .flags import RED_FLAG_LABELS
from .lang import language_name
from .models import (
    FOLLOWERS_WORD,
    INSUFFICIENT,
    KIND_GROUP,
    Assessed,
    Card,
    PlatformData,
    PlatformMetrics,
    platform_key,
)
from .qualify import METRIC_LABELS, platform_label

DISCLAIMER = (
    "Квалификация предварительная, основана на публичных данных. Финальное решение "
    "принимается по внутренним правилам и данным CRM."
)

AUDIENCE_RU = {
    "traders": "трейдеры", "long_term_investors": "инвесторы-долгосрочники",
    "airdrop_hunters": "айрдроп-охотники", "beginners": "новички и финграмотность",
    "mixed": "смешанная", "off_target": "нецелевая",
}
AUDIENCE_GENITIVE = {
    "traders": "трейдеров", "long_term_investors": "инвесторов-долгосрочников",
    "airdrop_hunters": "айрдроп-охотников", "beginners": "новичков", "mixed": "смешанной",
    "off_target": "нецелевой",
}
CONVERSION_RU = {"high": "высокий", "medium": "средний", "low": "низкий"}
TOPICS_RU = {
    "spot": "спот", "futures": "фьючерсы", "altcoins": "альткоины", "education": "обучение",
    "signals": "сигналы", "other": "прочее",
}
TONE_RU = {"professional": "профессиональная", "neutral": "нейтральная", "hype": "хайповая",
           "aggressive": "агрессивная"}
FIT_RU = {"good": "подходит", "acceptable": "допустимо", "poor": "не подходит"}
AIRDROP_RU = {"dominant": "доминирует", "significant": "заметная", "minor": "небольшая", "none": "нет"}
INSUFFICIENT_RU = "недостаточно данных"


def _ru(mapping: dict[str, str], value: str) -> str:
    if value == INSUFFICIENT:
        return INSUFFICIENT_RU
    return mapping.get(value, value)


def _days_ago(days: Optional[int]) -> str:
    if days is None:
        return "нет данных"
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def _yes_no(value: Optional[bool]) -> str:
    if value is None:
        return "нет данных"
    return "есть" if value else "нет"


def _money(value: Optional[float], currency: str) -> str:
    if value is None:
        return "—"
    return f"${value:g}" if currency == "USD" else f"{value:g} {currency}"


def _evidence(assessed: Assessed, limit: int = 2) -> list[str]:
    lines = [f"  ↳ {e}" for e in assessed.evidence[:limit]]
    if assessed.note:
        lines.append(f"  ↳ ({assessed.note})")
    return lines


# --------------------------------------------------------------------------
# Секции
# --------------------------------------------------------------------------

def platforms_line(card: Card) -> str:
    parts = []
    for data in card.platforms:
        label = platform_label(data)
        if data.ok:
            word = "участников" if data.kind == KIND_GROUP else FOLLOWERS_WORD.get(data.platform, "подписчиков")
            approx = "≈" if data.approximate else ""
            parts.append(f"{label} {approx}{num(data.followers)} {word}")
        else:
            parts.append(f"{label} — данные недоступны")
    return " | ".join(parts) or "—"


def _platform_brief(data: PlatformData, m: Optional[PlatformMetrics]) -> str:
    head = f"{platform_label(data)} {data.display_handle}"
    if not data.ok:
        return f"{head}: данные недоступны — {data.status_reason}"
    parts = [f"{num(data.followers)} {'участников' if data.kind == KIND_GROUP else FOLLOWERS_WORD.get(data.platform, 'подписчиков')}"]
    if m is not None:
        if m.median_views is not None:
            er = f" (ER {m.er}%)" if m.er is not None else ""
            parts.append(f"медиана {num(m.median_views)}{er}")
        unit = "видео" if data.platform == "youtube" else ("сообщений" if data.kind == KIND_GROUP else "постов")
        parts.append(f"{unit} за 30 дней: {m.posts_30d}{'+' if m.posts_30d_capped else ''}")
        parts.append(f"последний: {_days_ago(m.days_since_last_post)}")
    return f"{head}: " + ", ".join(parts)


def metrics_section(card: Card) -> str:
    primary = card.primary
    m = card.primary_metrics
    if primary is None or m is None:
        lines = ["Нет данных ни по одной площадке."]
        lines += [f"- {_platform_brief(d, None)}" for d in card.platforms]
        return "\n".join(lines)

    is_video = primary.platform == "youtube"
    unit = "видео" if is_video else "постов"
    lines = [f"Подписчики (макс. площадка): {num(primary.followers)} ({platform_label(primary)})"]
    if m.median_views is not None:
        er = f" (ER {m.er}%)" if m.er is not None else ""
        lines.append(
            f"Медиана просмотров: {num(m.median_views)}{er} | среднее: {num(m.avg_views)} "
            f"| выборка: {m.views_sample} {unit}"
        )
    elif primary.kind == KIND_GROUP:
        lines.append("Просмотры: не применимо (это чат, а не канал)")
    else:
        lines.append("Медиана просмотров: нет данных")
    lines.append(
        f"{'Видео' if is_video else 'Постов'} за 30 дней: {m.posts_30d}{'+' if m.posts_30d_capped else ''} "
        f"| Последн{'ее видео' if is_video else 'ий пост'}: {_days_ago(m.days_since_last_post)}"
    )
    reactions = num(m.avg_reactions) if m.avg_reactions is not None else "нет данных"
    comments = num(m.avg_comments) if m.avg_comments is not None else "нет данных"
    if primary.platform == "telegram":
        community = _yes_no(m.has_community_chat)
        if m.community_members:
            community += f" ({num(m.community_members)} участников)"
        lines.append(f"Комьюнити-чат: {community} | Среднее реакций: {reactions}")
        if m.avg_comments is not None:
            lines.append(f"Среднее комментариев: {comments}")
    else:
        likes = "лайков" if is_video else "лайков и репостов"
        lines.append(f"Среднее {likes}: {reactions} | Среднее комментариев: {comments}")
    if m.ads_window:
        if m.sells_ads:
            shown = ", ".join(m.ads_markers[:4])
            lines.append(f"Продаёт рекламу: да (найдены маркеры в {m.ads_posts} {'видео' if is_video else 'постах'} из {m.ads_window}: {shown})")
        else:
            lines.append(f"Продаёт рекламу: маркеров не найдено (проверено {m.ads_window} {unit})")
    if m.competitor_ref_posts:
        lines.append(f"Реферальные ссылки на биржи: в {m.competitor_ref_posts} постах")
    lines.append(f"Контакт: {m.contact or 'не найден в описании'}")

    others = [d for d in card.platforms if platform_key(d) != card.primary_key]
    if others:
        lines.append("Другие площадки:")
        for data in others:
            lines.append(f"- {_platform_brief(data, card.metrics.get(platform_key(data)))}")

    notes: list[str] = []
    for data in card.platforms:
        dm = card.metrics.get(platform_key(data))
        notes += [f"{platform_label(data)}: {n}" for n in data.notes]
        if dm and dm.young_posts_excluded:
            notes.append(f"{platform_label(data)}: свежие посты не вошли в медиану, ещё набирают просмотры ({dm.young_posts_excluded})")
        if dm and dm.shorts_excluded:
            notes.append(f"{platform_label(data)}: Shorts исключены из медианы ({dm.shorts_excluded})")
    lines += [f"  * {n}" for n in notes]
    return "\n".join(lines)


def audience_section(card: Card) -> str:
    geo = card.geo
    a = card.audience
    if geo.source == "manual":
        geo_line = f"Гео: {geo.country_name} (указано менеджером)"
    elif geo.country:
        confirm = " — подтвердить у партнёра" if geo.needs_confirmation else ""
        geo_line = f"Гео: {geo.country_name} (уверенность: {geo.confidence}{confirm})"
    else:
        geo_line = "Гео: не определено (уверенность: low — подтвердить у партнёра)"
    lines = [geo_line]
    best = [s for s in geo.signals if s.country == geo.country][:3]
    if best and geo.source != "manual":
        lines.append("  сигналы: " + "; ".join(s.detail for s in best))
    if geo.alternatives and geo.source != "manual":
        lines.append("  альтернативы: " + ", ".join(code for code, _ in geo.alternatives))

    lang = card.language
    if lang.primary != "unknown":
        lines.append(f"Язык: {language_name(lang.primary)} ({lang.share:.0%} постов)")
    else:
        lines.append("Язык: не определён")

    if not a.available:
        lines.append(f"Оценка аудитории LLM: недоступна — {a.error}")
        return "\n".join(lines)

    audience = _ru(AUDIENCE_RU, a.audience_type.value)
    if a.audience_type.known and a.audience_secondary not in ("none", INSUFFICIENT):
        audience += f" с частью {AUDIENCE_GENITIVE.get(a.audience_secondary, a.audience_secondary)}"
    lines.append(f"Тип аудитории: {audience}")
    lines += _evidence(a.audience_type)
    lines.append(f"Прогноз конверсии в объём: {_ru(CONVERSION_RU, a.conversion.value)}")
    if a.conversion_reason:
        lines.append(f"Обоснование: {a.conversion_reason}")
    lines += _evidence(a.conversion, 1)
    topics = ", ".join(TOPICS_RU.get(t, t) for t in a.topic_list) or INSUFFICIENT_RU
    lines.append(f"Тематика: {topics}")
    lines.append(
        f"Айрдроп-контент: {_ru(AIRDROP_RU, a.airdrop_share.value)} | "
        f"Тональность: {_ru(TONE_RU, a.tone.value)} | Под бренд: {_ru(FIT_RU, a.brand_fit)}"
    )
    lines += _evidence(a.tone, 1)
    if a.red_flags:
        lines.append("Красные флаги контента:")
        for flag in a.red_flags:
            mark = "" if flag.verified else " [цитата не найдена в постах — проверить]"
            comment = f" — {flag.comment}" if flag.comment else ""
            lines.append(f"  - {RED_FLAG_LABELS.get(flag.type, flag.type)}: «{flag.quote}»{comment}{mark}")
    elif a.red_flags_status == "none_found":
        lines.append("Красные флаги контента: не найдены")
    else:
        lines.append(f"Красные флаги контента: {INSUFFICIENT_RU}")
    if a.quotes_total:
        lines.append(f"Цитаты LLM сверены с постами: {a.quotes_verified} из {a.quotes_total}")
    return "\n".join(lines)


def qualification_section(card: Card) -> str:
    q = card.qualification
    lines = [f"Тип партнёра: {q.affiliate_type}"]
    if q.tier_rate is not None:
        basis = []
        for metric in q.tier_met_by:
            have = q.followers_single_platform if metric == "followers_single_platform" else q.community_members
            basis.append(f"{METRIC_LABELS[metric]}: {num(have)} ≥ {num(q.tier_thresholds.get(metric))}")
        lines.append(f"Предполагаемый тир: {q.tier_rate:g}%" + (f" ({'; '.join(basis)})" if basis else ""))
    else:
        lowest = f"{q.lowest_tier_rate:g}%" if q.lowest_tier_rate is not None else "—"
        lines.append(f"Предполагаемый тир: ниже минимального ({lowest})")
    if q.next_tier_rate is not None and q.next_tier_gaps:
        gaps = " или ".join(f"{num(g.missing)} {METRIC_LABELS[g.metric]}" for g in q.next_tier_gaps)
        label = "До следующего тира" if q.tier_rate is not None else "До минимального тира"
        lines.append(f"{label} ({q.next_tier_rate:g}%): не хватает {gaps}")
    elif q.tier_rate is not None:
        lines.append("До следующего тира: это максимальный тир")
    if q.community_members:
        lines.append(f"Комьюнити: {num(q.community_members)} участников ({q.community_source})")

    geo = card.geo.country or "гео не определено"
    if q.cpa is not None:
        how = " (региональная ставка)" if q.cpa_matched_by == "region" else ""
        lines.append(f"CPA по гео ({geo}): {_money(q.cpa, q.cpa_currency)} за {q.cpa_event}{how}")
    else:
        lines.append(f"CPA по гео ({geo}): ставки нет")
    if q.manual_review:
        lines.append("ТРЕБУЕТ РУЧНОЙ ПРОВЕРКИ:")
        lines += [f"  - {r}" for r in q.manual_review_reasons]
    return "\n".join(lines)


def flags_section(card: Card) -> str:
    if not card.flags:
        return "нет"
    lines = []
    for flag in card.flags:
        mark = "[!!]" if flag.severity == "critical" else "[!]"
        lines.append(f"{mark} {flag.code} — {flag.explanation}")
        if flag.evidence:
            lines.append(f"    основание: {flag.evidence}")
    return "\n".join(lines)


def warnings_lines(card: Card) -> str:
    return "\n".join(f"⚠ {w}" for w in card.warnings)


def render_markdown(card: Card, template: Optional[str] = None) -> str:
    template = template or (
        "ПАРТНЁР: ${partner}\nПлощадки: ${platforms}\n\nМЕТРИКИ\n${metrics}\n\n"
        "АУДИТОРИЯ\n${audience}\n\nКВАЛИФИКАЦИЯ\n${qualification}\n\nФЛАГИ\n${flags}\n\n"
        "РЕКОМЕНДАЦИЯ\n${recommendation}\n\nЧЕРНОВИК ЗАЯВКИ\n${draft}\n---\n${disclaimer}\n${warnings}\n"
    )
    draft_note = "" if card.draft_source == "llm" else "(текстовые блоки собраны локально, без LLM)\n"
    partner = card.partner_name
    if card.partner_handle and card.partner_handle != card.partner_name:
        partner = f"{card.partner_name} ({card.partner_handle})"
    text = Template(template).safe_substitute(
        partner=partner,
        platforms=platforms_line(card),
        metrics=metrics_section(card),
        audience=audience_section(card),
        qualification=qualification_section(card),
        flags=flags_section(card),
        recommendation="\n".join(card.recommendation),
        draft=draft_note + card.draft.strip(),
        disclaimer=DISCLAIMER,
        warnings=warnings_lines(card),
    )
    return text.rstrip() + "\n"


# --------------------------------------------------------------------------
# JSON и CSV
# --------------------------------------------------------------------------

def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def card_to_dict(card: Card, include_items: bool = False) -> dict[str, Any]:
    platforms = []
    for data in card.platforms:
        item = data.to_dict()
        if not include_items:
            item.pop("items", None)
        item["metrics"] = _plain(card.metrics[platform_key(data)].to_dict()) if platform_key(data) in card.metrics else None
        platforms.append(item)
    return {
        "partner": {"name": card.partner_name, "handle": card.partner_handle},
        "generated_at": card.generated_at.isoformat(),
        "request": _plain(card.request),
        "primary_platform": card.primary_key,
        "platforms": platforms,
        "language": _plain(card.language),
        "geo": {**_plain(card.geo), "needs_confirmation": card.geo.needs_confirmation},
        "audience": _plain(card.audience),
        "flags": _plain(card.flags),
        "qualification": _plain(card.qualification),
        "deal": _plain(card.deal),
        "recommendation": card.recommendation,
        "draft": card.draft,
        "draft_source": card.draft_source,
        "warnings": card.warnings,
        "disclaimer": DISCLAIMER,
        "report_path": card.report_path,
    }


def render_json(cards: Iterable[Card] | Card) -> str:
    if isinstance(cards, Card):
        return json.dumps(card_to_dict(cards), ensure_ascii=False, indent=2)
    return json.dumps([card_to_dict(c) for c in cards], ensure_ascii=False, indent=2)


CSV_COLUMNS = [
    "checked_at", "status", "partner", "handle", "links", "primary_platform", "followers_max",
    "telegram", "youtube", "x", "community_members", "median_views", "er", "posts_30d",
    "days_since_last_post", "avg_reactions", "community_chat", "sells_ads", "contact",
    "language", "geo", "geo_confidence", "audience_type", "conversion", "affiliate_type",
    "tier", "next_tier", "next_tier_gap", "cpa", "manual_review", "flags", "recommendation",
    "report_path",
]


def card_csv_row(card: Card) -> dict[str, Any]:
    m = card.primary_metrics
    q = card.qualification
    by_platform: dict[str, Any] = {}
    for data in card.platforms:
        if data.ok and data.kind != KIND_GROUP:
            by_platform[data.platform] = max(by_platform.get(data.platform) or 0, data.followers or 0)
    gap = "; ".join(f"{g.missing} {g.metric}" for g in q.next_tier_gaps)
    return {
        "checked_at": card.generated_at.strftime("%Y-%m-%d %H:%M"),
        "status": "ok" if card.primary_key else "нет данных: " + "; ".join(
            d.status_reason for d in card.platforms if not d.ok) or "нет данных",
        "partner": card.partner_name,
        "handle": card.partner_handle,
        "links": " ".join(d.url for d in card.platforms),
        "primary_platform": card.primary.platform if card.primary else "",
        "followers_max": q.followers_single_platform or "",
        "telegram": by_platform.get("telegram", ""),
        "youtube": by_platform.get("youtube", ""),
        "x": by_platform.get("x", ""),
        "community_members": q.community_members or "",
        "median_views": m.median_views if m and m.median_views is not None else "",
        "er": m.er if m and m.er is not None else "",
        "posts_30d": m.posts_30d if m else "",
        "days_since_last_post": m.days_since_last_post if m and m.days_since_last_post is not None else "",
        "avg_reactions": m.avg_reactions if m and m.avg_reactions is not None else "",
        "community_chat": _yes_no(m.has_community_chat) if m else "",
        "sells_ads": ("да" if m.sells_ads else "нет") if m and m.ads_window else "",
        "contact": m.contact if m and m.contact else "",
        "language": card.language.primary,
        "geo": card.geo.country or "",
        "geo_confidence": card.geo.confidence,
        "audience_type": card.audience.audience_type.value if card.audience.available else "",
        "conversion": card.audience.conversion.value if card.audience.available else "",
        "affiliate_type": q.affiliate_type,
        "tier": f"{q.tier_rate:g}" if q.tier_rate is not None else "",
        "next_tier": f"{q.next_tier_rate:g}" if q.next_tier_rate is not None else "",
        "next_tier_gap": gap,
        "cpa": f"{q.cpa:g}" if q.cpa is not None else "",
        "manual_review": "да" if q.manual_review else "нет",
        "flags": ", ".join(f.code for f in card.flags),
        "recommendation": " ".join(card.recommendation),
        "report_path": card.report_path or "",
    }


def skipped_csv_row(links: list[str], reason: str) -> dict[str, Any]:
    row = {c: "" for c in CSV_COLUMNS}
    row.update({"status": reason, "links": " ".join(links), "partner": " ".join(links)})
    return row


def sort_key(row: dict[str, Any]) -> tuple:
    """Пакетный режим: сначала по тиру, затем по ER — по убыванию."""
    def _f(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0

    return (-_f(row.get("tier")), -_f(row.get("er")))


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=sort_key):
            writer.writerow(row)
    return path


def csv_text(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in sorted(rows, key=sort_key):
        writer.writerow(row)
    return buffer.getvalue()
