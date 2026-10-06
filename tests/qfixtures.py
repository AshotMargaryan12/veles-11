"""Общие заготовки для тестов квалификатора: данные площадок, конфиг, фейковый LLM."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from qualifier.config import (
    QualifierConfig,
    Settings,
    load_cpa,
    load_criteria,
    load_geo_profiles,
    load_thresholds,
    load_volume_criteria,
)
from qualifier.models import ContentItem, PlatformData

QNOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[1]

# Тестовые пороги и ставки — ВЫМЫШЛЕННЫЕ, структура как в criteria.yaml.
# Реальные внутренние числа в репозиторий не кладутся (раздел 11.5 ТЗ).
SPEC_CRITERIA = """
affiliate_types:
  individual:
    tiers:
      - rate: 48
        followers_single_platform: 16000
        community_members: 11000
        followers_with_views: {followers: 400000, avg_views: 40000}
      - rate: 38
        followers_single_platform: 11000
        community_members: 8500
      - rate: 28
        followers_single_platform: 6000
        community_members: 3500
  institutional:
    tiers:
      - rate: 48
        followers_single_platform: 22000
      - rate: 38
        followers_single_platform: 16000
      - rate: 28
        followers_single_platform: 11000
cpa_rates_file: cpa_by_country.csv
borderline_margin: 0.10
"""

SPEC_CPA = """country_code,country,region,cpa,currency,event
EG,Египет,MENA,12,USD,FTT
RU,Россия,CIS,7,USD,FTT
*,,MENA,5,USD,FTT
"""


def make_config_dir(tmp_path: Path, criteria: str = SPEC_CRITERIA, cpa: Optional[str] = SPEC_CPA,
                    volume: bool = True) -> Path:
    config_dir = tmp_path / "qconfig"
    config_dir.mkdir()
    (config_dir / "criteria.yaml").write_text(criteria, encoding="utf-8")
    if cpa is not None:
        (config_dir / "cpa_by_country.csv").write_text(cpa, encoding="utf-8")
    src = REPO / "config" / "qualifier"
    for name in ("thresholds.yaml", "geo_markers.yaml"):
        (config_dir / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    if volume:
        # пример с вымышленными числами — под именем «реального» файла
        (config_dir / "volume_criteria.yaml").write_text(
            (src / "volume_criteria.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return config_dir


def make_settings(tmp_path: Path, config_dir: Optional[Path] = None, **overrides: Any) -> Settings:
    settings = Settings(
        config_dir=str(config_dir or make_config_dir(tmp_path)),
        templates_dir=str(REPO / "templates"),
        reports_dir=str(tmp_path / "reports"),
        db_path=str(tmp_path / "q.db"),
        batch_pause_sec=0,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def make_config(settings: Settings) -> QualifierConfig:
    criteria = load_criteria(settings.config_dir)
    return QualifierConfig(
        criteria=criteria,
        cpa=load_cpa(settings.config_dir, criteria.cpa_rates_file),
        thresholds=load_thresholds(settings.config_dir),
        geo=load_geo_profiles(settings.config_dir),
        templates_dir=Path(settings.templates_dir),
        volume=load_volume_criteria(settings.config_dir),
    )


def items(
    count: int = 30,
    views: int = 1800,
    reactions: Optional[int] = 40,
    text: str = "Разбор рынка BTC: сделка на фьючерсах, стоп и тейк",
    step_hours: float = 24,
    start_hours: float = 30,
    now: datetime = QNOW,
    views_list: Optional[list[int]] = None,
) -> list[ContentItem]:
    result = []
    for i in range(count):
        result.append(ContentItem(
            id=str(i + 1),
            date=now - timedelta(hours=start_hours + i * step_hours),
            text=text if isinstance(text, str) else text[i % len(text)],
            views=views_list[i] if views_list else views,
            reactions=reactions,
        ))
    return result


def tg_data(
    handle: str = "good_channel",
    followers: int = 13556,
    posts: Optional[list[ContentItem]] = None,
    description: str = "Канал про трейдинг. По рекламе: @adv_manager",
    linked: bool = True,
    members: Optional[int] = 1200,
    title: str = "Хороший канал",
) -> PlatformData:
    return PlatformData(
        platform="telegram", handle=handle, url=f"https://t.me/{handle}", title=title,
        username=handle, description=description, followers=followers,
        has_community_chat=linked, community_members=members if linked else None,
        community_title="Чат" if linked else None,
        items=posts if posts is not None else items(), items_limit=50, source="test",
    )


class FakeMessages:
    """Подмена client.messages: отдаёт заданные JSON-ответы и запоминает запросы."""

    def __init__(self, responses: list[dict[str, Any]], stop_reason: str = "end_turn"):
        self.responses = list(responses)
        self.stop_reason = stop_reason
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        payload = self.responses.pop(0) if self.responses else {}
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
        )


class FakeAnthropic:
    def __init__(self, responses: list[dict[str, Any]], stop_reason: str = "end_turn"):
        self.messages = FakeMessages(responses, stop_reason)


def assessment(**overrides: Any) -> dict[str, Any]:
    data = {
        "audience_type": {"value": "traders", "secondary": "beginners",
                          "evidence": ["«Разбор рынка BTC: сделка на фьючерсах» [TG#1]"]},
        "conversion_forecast": {"value": "high", "reason": "аудитория уже торгует",
                                "evidence": ["регулярные разборы сделок"]},
        "topic_focus": {"values": ["futures"], "status": "ok", "evidence": ["фьючерсы"]},
        "red_flags": {"status": "none_found", "items": []},
        "tone": {"value": "professional", "brand_fit": "good", "evidence": ["спокойная подача"]},
        "airdrop_share": {"value": "none", "evidence": ["нет айрдропов"]},
        "geo_hint": {"country": "insufficient_data", "evidence": ["нет местных реалий"]},
    }
    data.update(overrides)
    return data


class StaticCollector:
    """Сборщик, который отдаёт заранее заданные PlatformData и считает обращения."""

    def __init__(self, platform: str, data: dict[str, PlatformData]):
        self.platform = platform
        self.data = data
        self.calls: list[str] = []

    def collect(self, ref):
        self.calls.append(ref.handle)
        found = self.data.get(ref.handle.lower())
        if found is None:
            return PlatformData(platform=ref.platform, handle=ref.handle, url=ref.url,
                                status="not_found", status_reason="нет такого канала")
        return PlatformData.from_dict(found.to_dict())

    def close(self):
        pass
