"""Объёмы партнёра: автооценка тира, whitelisting по объёмам и ROI.

Логика повторяет внутренние документы (параметры — в volume_criteria.yaml):
  * автооценка — как в ROI-шаблоне: объём и новые трейдеры за период оценки
    (месячные × evaluation_months) против порогов тира; ни один тир не взят —
    ставка по умолчанию;
  * whitelisting (критерий 1 регламента) — для тех, кто не прошёл автооценку:
    основной показатель >= N% порога и второй >= M%, или основной >= N% и
    партнёр в топе по FTT своего региона; правило задаёт срок (1 или 3 месяца).
    FTT может заменить новых трейдеров, если их не хватает;
  * ROI — как в шаблоне: комиссия бирже с taker/maker-объёма, выплата партнёру
    по ставке тира плюс фикс, ROI = комиссия / выплата − 1, отдельно с LTV и
    сравнение с предложением конкурента.

Считается только локально: объёмы и ставки в LLM не уходят.
"""

from __future__ import annotations

import re
from typing import Optional

from .config import MARKETS, MarketCriteria, VolumeCriteria
from .models import MarketResult, PerformanceInput, PerformanceResult, RoiResult

MARKET_TITLES = {"spot": "Spot", "futures": "Futures"}


def _pct(have: float, need: float) -> float:
    return have / need * 100 if need else 0.0


def _money(value: float) -> str:
    if value >= 1e9:
        return f"${value / 1e9:.2f}B".replace(".00B", "B")
    if value >= 1e6:
        return f"${value / 1e6:.2f}M".replace(".00M", "M")
    if value >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:.0f}"


def evaluate_market(
    market: str,
    criteria: MarketCriteria,
    vc: VolumeCriteria,
    volume_month: float,
    new_traders_month: int,
    ftt_month: int,
    top_ftt: bool,
) -> MarketResult:
    months = vc.evaluation_months
    volume = volume_month * months
    traders = new_traders_month * months
    ftt = ftt_month * months
    traders_eff = max(traders, ftt)

    result = MarketResult(
        market=market,
        volume_month=volume_month,
        volume_period=volume,
        new_traders_period=traders_eff,
        ftt_used=ftt > traders,
        auto_rate_volume=criteria.default_rate,
        auto_rate_full=criteria.default_rate,
        default_rate=criteria.default_rate,
        invitee_limit=criteria.invitee_limit,
    )

    # --- автооценка (ROI-шаблон): высший тир, где выполнено условие ------------
    for tier in criteria.tiers:
        if volume >= tier.volume:
            result.auto_rate_volume = tier.rate
            break
    for tier in criteria.tiers:
        if volume >= tier.volume and traders_eff >= tier.new_traders:
            result.auto_rate_full = tier.rate
            break

    # --- whitelisting (критерий 1): все подходящие варианты -------------------
    options: list[tuple[float, int, str]] = []
    for tier in criteria.tiers:
        vol_pct = _pct(volume, tier.volume)
        nt_pct = _pct(traders_eff, tier.new_traders)
        for rule in vc.eligibility:
            basis = ""
            if rule.requires_top_ftt:
                if top_ftt and (vol_pct >= rule.primary_pct or nt_pct >= rule.primary_pct):
                    lead = "объём" if vol_pct >= rule.primary_pct else "новые трейдеры"
                    basis = f"{lead} ≥ {rule.primary_pct:g}% порога и топ по FTT в регионе"
            elif vol_pct >= rule.primary_pct and nt_pct >= rule.secondary_pct:
                basis = f"объём {vol_pct:.0f}% и новые трейдеры {nt_pct:.0f}% порога"
            elif nt_pct >= rule.primary_pct and vol_pct >= rule.secondary_pct:
                basis = f"новые трейдеры {nt_pct:.0f}% и объём {vol_pct:.0f}% порога"
            if basis:
                options.append((tier.rate, rule.months, basis))
                break  # правила отсортированы от длинного срока к короткому

    if options:
        best = max(options, key=lambda o: (o[0], o[1]))
        result.whitelist_rate, result.whitelist_months, result.whitelist_basis = best
        longer = [o for o in options if o[1] > best[1]]
        if longer:
            alt = max(longer, key=lambda o: (o[0], o[1]))
            result.alternative = (alt[0], alt[1])

    # --- до следующего тира -----------------------------------------------------
    reached = max(result.auto_rate_full, result.whitelist_rate or 0)
    higher = [t for t in criteria.tiers if t.rate > reached]
    if higher:
        nxt = min(higher, key=lambda t: t.rate)
        parts = []
        if volume < nxt.volume:
            parts.append(f"объёма {_money(nxt.volume - volume)} за {months} мес")
        if traders_eff < nxt.new_traders:
            parts.append(f"{nxt.new_traders - traders_eff} новых трейдеров")
        if parts:
            result.next_gap = f"до {nxt.rate:g}%: не хватает " + " и ".join(parts)
    return result


def _segments(volume: float, taker_share: float, taker_fee: float, maker_fee: float,
              rate: float, prefix: str) -> list[dict[str, float]]:
    taker = volume * taker_share
    maker = volume - taker
    rows = []
    for name, vol, fee_rate in ((f"{prefix} taker", taker, taker_fee), (f"{prefix} maker", maker, maker_fee)):
        fee = vol * fee_rate
        rows.append({"name": name, "volume": vol, "fee": fee, "rebate": fee * rate / 100})
    return rows


def compute_roi(perf: PerformanceInput, vc: VolumeCriteria, spot_rate: float, futures_rate: float) -> RoiResult:
    fees, comp = vc.fees, vc.competitor_fees
    segments = _segments(perf.spot_volume, vc.taker_share["spot"], fees["spot_taker"],
                         fees["spot_maker"], spot_rate, "Spot")
    segments += _segments(perf.futures_volume, vc.taker_share["futures"], fees["futures_taker"],
                          fees["futures_maker"], futures_rate, "Futures")
    roi = RoiResult(segments=segments)
    roi.revenue = sum(s["fee"] for s in segments)
    roi.rebates = sum(s["rebate"] for s in segments)
    roi.upfront = perf.upfront
    roi.cost = roi.rebates + roi.upfront
    if roi.cost > 0:
        roi.roi = roi.revenue / roi.cost - 1
        if perf.ltv and perf.ftt:
            roi.roi_ltv = (perf.ltv * perf.ftt + roi.revenue) / roi.cost - 1

    if perf.competitor_spot_rate is not None or perf.competitor_futures_rate is not None:
        c_spot = _segments(perf.spot_volume, vc.taker_share["spot"], comp["spot_taker"],
                           comp["spot_maker"], perf.competitor_spot_rate or 0, "Spot")
        c_fut = _segments(perf.futures_volume, vc.taker_share["futures"], comp["futures_taker"],
                          comp["futures_maker"], perf.competitor_futures_rate or 0, "Futures")
        roi.competitor = True
        roi.competitor_revenue = sum(s["fee"] for s in c_spot + c_fut)
        roi.competitor_rebates = sum(s["rebate"] for s in c_spot + c_fut)
        roi.competitor_upfront = perf.competitor_upfront
        roi.competitor_cost = roi.competitor_rebates + roi.competitor_upfront
        if roi.competitor_cost > 0:
            roi.competitor_roi = roi.competitor_revenue / roi.competitor_cost - 1
        roi.vs_competitor = roi.cost - roi.competitor_cost
    return roi


def evaluate(perf: PerformanceInput, vc: VolumeCriteria) -> PerformanceResult:
    result = PerformanceResult(input=perf, evaluation_months=vc.evaluation_months,
                               criteria_are_examples=vc.is_example)
    inputs = {
        "spot": (perf.spot_volume, perf.spot_new_traders),
        "futures": (perf.futures_volume, perf.futures_new_traders),
    }
    for market in MARKETS:
        volume, traders = inputs[market]
        m = evaluate_market(market, vc.markets[market], vc, volume, traders, perf.ftt, perf.top_ftt_region)
        proposed = perf.proposed_spot_rate if market == "spot" else perf.proposed_futures_rate
        if proposed is not None:
            m.rate_used = proposed
        else:
            m.rate_used = max(m.auto_rate_full, m.whitelist_rate or 0)
        result.markets[market] = m

    result.roi = compute_roi(perf, vc, result.markets["spot"].rate_used, result.markets["futures"].rate_used)

    if perf.external:
        result.notes.append(
            f"объёмы с {perf.source_label}: по регламенту нужны скриншоты из кабинета партнёра"
        )
    if any(m.ftt_used for m in result.markets.values()):
        result.notes.append("новых трейдеров не хватало — вместо них засчитаны FTT")
    limits = [f"{MARKET_TITLES[k]}: {m.invitee_limit}" for k, m in result.markets.items() if m.invitee_limit]
    if limits:
        result.notes.append("проверить в CRM условие на приглашённых — " + "; ".join(limits))
    if vc.is_example:
        result.notes.append(
            "критерии по объёмам — ПРИМЕРЫ-ЗАГЛУШКИ: выполните qualify config import"
        )
    return result


def _exact(value: float) -> str:
    return f"${value:,.0f}".replace(",", " ")


def market_line(m: MarketResult, months: int) -> str:
    """Строка для карточки: что дала автооценка и whitelisting."""
    title = MARKET_TITLES[m.market]
    if not m.volume_month and not m.new_traders_period:
        return f"{title}: объёмов нет"
    head = (f"{title}: {_money(m.volume_month)}/мес → {_money(m.volume_period)} за {months} мес, "
            f"новых трейдеров {m.new_traders_period}" + (" (по FTT)" if m.ftt_used else ""))
    if m.auto_passed and m.needs_whitelist:
        verdict = (f"проходит автооценку на {m.auto_rate_full:g}%; whitelisting даёт "
                   f"{m.whitelist_rate:g}% на {m.whitelist_months} мес ({m.whitelist_basis})")
    elif m.auto_passed:
        verdict = f"проходит автооценку на {m.auto_rate_full:g}% — whitelisting не нужен"
    elif m.whitelist_rate is not None:
        verdict = f"whitelisting {m.whitelist_rate:g}% на {m.whitelist_months} мес ({m.whitelist_basis})"
        if m.alternative:
            verdict += f"; или {m.alternative[0]:g}% на {m.alternative[1]} мес"
    else:
        verdict = f"критерий 1 не выполнен, ставка по умолчанию {m.default_rate:g}%"
    return f"{head}\n    {verdict}" + (f"\n    {m.next_gap}" if m.next_gap else "")


def roi_lines(perf: PerformanceResult) -> list[str]:
    roi = perf.roi
    if roi is None:
        return []
    spot, fut = perf.markets["spot"], perf.markets["futures"]
    lines = [
        f"ROI в месяц (Spot {spot.rate_used:g}% / Futures {fut.rate_used:g}%): "
        f"комиссия бирже {_exact(roi.revenue)}, партнёру {_exact(roi.rebates)}"
        + (f" + фикс {_exact(roi.upfront)}" if roi.upfront else "")
        + (f" → ROI {roi.roi:.2f}" if roi.roi is not None else " → ROI не считается (выплата 0)")
    ]
    if roi.roi_ltv is not None:
        lines.append(f"ROI с LTV новых трейдеров: {roi.roi_ltv:.2f} (может быть завышен)")
    if roi.competitor:
        sign = "больше" if roi.vs_competitor > 0 else "меньше"
        lines.append(
            f"Конкурент: партнёр получит {_exact(roi.competitor_cost)}/мес, у нас {_exact(roi.cost)}/мес "
            f"— у нас на {_exact(abs(roi.vs_competitor))} {sign}"
        )
    return lines


def best_rates(perf: PerformanceResult) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Ставки по рынкам и срок, которые можно запрашивать по объёмам."""
    rates: dict[str, Optional[float]] = {}
    months: list[int] = []
    for key, m in perf.markets.items():
        if m.needs_whitelist:
            rates[key] = m.whitelist_rate
            months.append(m.whitelist_months or 0)
        elif m.auto_passed:
            rates[key] = m.auto_rate_full
        else:
            rates[key] = None
    return rates["spot"], rates["futures"], (min(months) if months else None)


# --------------------------------------------------------------------------
# Ввод: CLI, строка пакетного файла, веб-форма
# --------------------------------------------------------------------------

_MONEY_RE = re.compile(r"^([\d.,]+)(k|m|b|тыс|млн|млрд)?$", re.IGNORECASE)
_MULT = {"k": 1e3, "тыс": 1e3, "m": 1e6, "млн": 1e6, "b": 1e9, "млрд": 1e9}

# ключ поля -> (атрибут PerformanceInput, тип)
PERFORMANCE_FIELDS: dict[str, tuple[str, str]] = {
    "source": ("source", "text"),
    "spot_volume": ("spot_volume", "money"),
    "futures_volume": ("futures_volume", "money"),
    "spot_traders": ("spot_new_traders", "int"),
    "futures_traders": ("futures_new_traders", "int"),
    "ftt": ("ftt", "int"),
    "top_ftt": ("top_ftt_region", "bool"),
    "upfront": ("upfront", "money"),
    "ltv": ("ltv", "money"),
    "spot_rate": ("proposed_spot_rate", "rate"),
    "futures_rate": ("proposed_futures_rate", "rate"),
    "comp_spot": ("competitor_spot_rate", "rate"),
    "comp_futures": ("competitor_futures_rate", "rate"),
    "comp_upfront": ("competitor_upfront", "money"),
}


def parse_money(value: object) -> float:
    """«1.2M», «200k», «$3,000,000», «3 000 000», «2,5 млн» -> число. Ошибка — ValueError."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().lower().replace("$", "").replace(" ", "").replace(" ", "").replace("_", "")
    if not text:
        return 0.0
    match = _MONEY_RE.match(text)
    if not match:
        raise ValueError(f"не понял сумму «{value}»: пишите 1500000, 1.5M или 200k")
    number, suffix = match.group(1), match.group(2)
    if "," in number and "." in number:
        number = number.replace(",", "")
    elif "," in number:
        head, _, tail = number.rpartition(",")
        number = f"{head.replace(',', '')}.{tail}" if suffix and len(tail) <= 2 else number.replace(",", "")
    return float(number) * _MULT.get(suffix or "", 1)


def _convert(kind: str, raw: object) -> object:
    if kind == "text":
        return str(raw or "").strip()
    if kind == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "да", "on")
    if kind == "int":
        return int(parse_money(raw))
    if kind == "rate":
        text = str(raw or "").strip().rstrip("%")
        return float(text.replace(",", ".")) if text else None
    return parse_money(raw)


def performance_from_mapping(values: dict[str, object]) -> Optional[PerformanceInput]:
    """Поля формы / ключи строки -> PerformanceInput. Пусто — None."""
    perf = PerformanceInput()
    touched = False
    for key, (attr, kind) in PERFORMANCE_FIELDS.items():
        raw = values.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        value = _convert(kind, raw)
        if value is None:
            continue
        setattr(perf, attr, value)
        touched = True
    return perf if touched and not perf.empty else None
