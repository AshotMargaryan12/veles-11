"""Оркестратор: ссылки партнёра -> карточка квалификации.

  ссылки -> сбор данных (кэш 7 дней) -> метрики -> язык -> LLM-оценка ->
  гео -> флаги -> квалификация и CPA -> рекомендация -> черновик -> история
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import QualifierConfig, Settings
from .draft import build_draft
from .flags import compute_flags
from .geo import estimate_geo
from .lang import detect_language
from .links import parse_links
from .llm import LLMClient, assess_audience
from .performance import evaluate as evaluate_performance
from .models import (
    PlatformData,
    PlatformMetrics,
    PlatformRef,
    Card,
    PartnerRequest,
    platform_key,
    utcnow,
)
from .qualify import qualify, recommend
from .render import card_to_dict, render_markdown
from .stats import compute_metrics, primary_key, sorted_items
from .store import Store

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    """Достигнут MAX_CHANNELS_PER_RUN — остальные площадки в этом запуске не опрашиваем."""


def default_collectors(settings: Settings) -> dict[str, Any]:
    from .platforms.telegram import TelegramCollector
    from .platforms.x import XCollector
    from .platforms.youtube import YouTubeCollector

    return {
        "telegram": TelegramCollector(settings),
        "youtube": YouTubeCollector(settings),
        "x": XCollector(settings),
    }


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return slug[:60] or "partner"


class Qualifier:
    def __init__(
        self,
        settings: Settings,
        config: QualifierConfig,
        store: Optional[Store] = None,
        collectors: Optional[dict[str, Any]] = None,
        llm: Optional[LLMClient] = None,
        use_llm: bool = True,
        now: Optional[datetime] = None,
    ):
        self.settings = settings
        self.config = config
        self.store = store
        self.collectors = collectors if collectors is not None else default_collectors(settings)
        if use_llm:
            self.llm = llm or LLMClient(settings, cache=store)
            if self.llm.cache is None:
                self.llm.cache = store
        else:
            self.llm = None
        self._now = now
        self.fetches = 0

    def now(self) -> datetime:
        return self._now or utcnow()

    def close(self) -> None:
        for collector in self.collectors.values():
            close = getattr(collector, "close", None)
            if close:
                close()

    # --- сбор ---------------------------------------------------------------

    def collect(self, ref: PlatformRef, refresh: bool = False) -> PlatformData:
        if self.store is not None and not refresh:
            cached = self.store.cache_get("platform", ref.key)
            if cached is not None:
                data = PlatformData.from_dict(cached)
                data.from_cache = True
                return data
        if self.fetches >= self.settings.max_channels_per_run:
            raise BudgetExceeded(
                f"достигнут лимит MAX_CHANNELS_PER_RUN={self.settings.max_channels_per_run}"
            )
        collector = self.collectors.get(ref.platform)
        if collector is None:
            raise ValueError(f"нет сборщика для площадки {ref.platform}")
        self.fetches += 1
        data = collector.collect(ref)
        if data.ok and self.store is not None:
            self.store.cache_set("platform", ref.key, data.to_dict())
        return data

    # --- карточка -------------------------------------------------------------

    def run(self, request: PartnerRequest) -> Card:
        refs = parse_links(request.links)
        now = self.now()
        platforms = [self.collect(ref, request.refresh) for ref in refs]
        return self.build_card(request, platforms, now)

    def build_card(self, request: PartnerRequest, platforms: list[PlatformData], now: datetime) -> Card:
        cfg = self.config
        th = cfg.thresholds
        metrics: dict[str, PlatformMetrics] = {
            platform_key(d): compute_metrics(d, th, now) for d in platforms
        }
        main_key = primary_key(platforms)

        texts: list[str] = []
        for data in platforms:
            if data.ok:
                texts += [data.title, data.description]
                texts += [i.text for i in sorted_items(data)]
        language = detect_language([t for t in texts if t])

        warnings: list[str] = []
        if self.llm is not None and request.refresh:
            self.llm.bypass_cache = True
        audience = assess_audience(self.llm, platforms, metrics, main_key)
        if self.llm is not None:
            self.llm.bypass_cache = False
        if self.llm is not None and audience.error:
            warnings.append(f"LLM-оценка не выполнена: {audience.error}")

        profile_countries = [d.country for d in platforms if d.ok and d.country]
        geo = estimate_geo(
            texts,
            language,
            cfg.geo,
            profile_countries=profile_countries,
            llm_country=audience.geo_hint.value if audience.available and audience.geo_hint.known else None,
            llm_evidence=audience.geo_hint.evidence if audience.available else None,
            manual=request.geo,
        )

        flags = compute_flags(platforms, metrics, audience, th)
        qual = qualify(platforms, cfg.criteria, cfg.cpa, geo, request.affiliate_type, metrics)

        # объёмы партнёра (наша биржа или другая) — критерий 1 и ROI, только локально
        perf = None
        if request.performance is not None and not request.performance.empty:
            if cfg.volume is None:
                warnings.append(
                    "объёмы указаны, но нет config/qualifier/volume_criteria.yaml — "
                    "выполните qualify config import"
                )
            else:
                perf = evaluate_performance(request.performance, cfg.volume)
                if cfg.volume.is_example:
                    warnings.append(
                        "КРИТЕРИИ ПО ОБЪЁМАМ — ПРИМЕРЫ-ЗАГЛУШКИ. Импортируйте ROI-шаблон и регламент: "
                        "qualify config import --roi ... --guidelines ..."
                    )
        for data in (d for d in platforms if not d.ok):
            qual.manual_review_reasons.append(
                f"{data.platform_title} {data.display_handle}: данные недоступны — {data.status_reason}"
            )
        qual.manual_review = bool(qual.manual_review_reasons)
        lines, deal = recommend(qual, flags, audience, geo, cfg.criteria, perf)

        primary = next((d for d in platforms if platform_key(d) == main_key), None)
        if primary is not None:
            partner_name = primary.title or primary.display_handle
            partner_handle = primary.display_handle
        else:
            first = platforms[0] if platforms else None
            partner_name = first.display_handle if first else "—"
            partner_handle = partner_name

        lang = self.settings.draft_language
        # оценка уже упала (нет ключа, ошибка API) — второй запрос упадёт так же
        draft_llm = self.llm if audience.available else None
        draft, draft_source, draft_error = build_draft(
            cfg.template("whitelist_request.md", lang),
            llm=draft_llm,
            partner_name=f"{partner_name} ({partner_handle})" if partner_name != partner_handle else partner_name,
            platforms=platforms,
            metrics=metrics,
            audience=audience,
            geo=geo,
            qual=qual,
            deal=deal,
            flags=flags,
            notes=request.notes,
            lang=lang,
            now=now,
            perf=perf,
        )
        if draft_error:
            warnings.append(f"черновик собран без LLM: {draft_error}")

        if cfg.criteria.is_example:
            warnings.append(
                "КРИТЕРИИ ТИРОВ — ПРИМЕРЫ-ЗАГЛУШКИ с вымышленными числами. Заполните "
                "config/qualifier/criteria.yaml (см. criteria.example.yaml)"
            )
        if cfg.cpa.is_example:
            warnings.append(
                "CPA-ставки — примеры-заглушки. Заполните config/qualifier/cpa_by_country.csv"
            )
        elif cfg.cpa.path is None:
            warnings.append("нет файла CPA-ставок config/qualifier/cpa_by_country.csv")
        for data in platforms:
            if data.from_cache:
                warnings.append(
                    f"{data.platform_title} {data.display_handle}: данные из кэша от "
                    f"{data.fetched_at:%Y-%m-%d %H:%M} UTC (обновить: --refresh)"
                )
        if audience.from_cache:
            warnings.append("оценка LLM взята из кэша")

        return Card(
            partner_name=partner_name,
            partner_handle=partner_handle,
            request=request,
            platforms=platforms,
            metrics=metrics,
            primary_key=main_key,
            language=language,
            geo=geo,
            audience=audience,
            flags=flags,
            qualification=qual,
            deal=deal,
            recommendation=lines,
            draft=draft,
            draft_source=draft_source,
            warnings=warnings,
            generated_at=now,
            performance=perf,
        )

    # --- сохранение -----------------------------------------------------------

    def save(self, card: Card, write_report: bool = True) -> Optional[Path]:
        path: Optional[Path] = None
        if write_report:
            reports = Path(self.settings.reports_dir)
            reports.mkdir(parents=True, exist_ok=True)
            name = f"{card.generated_at:%Y-%m-%d_%H%M}_{_slug(card.partner_handle.lstrip('@'))}.md"
            path = reports / name
            card.report_path = str(path)
            template = None
            try:
                template = self.config.template("card.md")
            except Exception:  # шаблон карточки необязателен
                template = None
            path.write_text(render_markdown(card, template), encoding="utf-8")
        if self.store is not None:
            m = card.primary_metrics
            q = card.qualification
            self.store.save_check({
                "created_at": card.generated_at.isoformat(),
                "partner": f"{card.partner_name} ({card.partner_handle})",
                "links": " ".join(d.url for d in card.platforms),
                "primary_platform": card.primary.platform if card.primary else None,
                "followers": q.followers_single_platform,
                "median_views": m.median_views if m else None,
                "er": m.er if m else None,
                "geo": card.geo.country,
                "geo_confidence": card.geo.confidence,
                "affiliate_type": q.affiliate_type,
                "tier": q.tier_rate,
                "cpa": q.cpa,
                "flags": ",".join(f.code for f in card.flags),
                "manual_review": int(q.manual_review),
                "report_path": card.report_path,
                "card_json": json.dumps(card_to_dict(card), ensure_ascii=False),
            })
        return path
