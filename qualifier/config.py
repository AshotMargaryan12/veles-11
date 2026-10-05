"""Конфигурация квалификатора: .env, criteria.yaml, thresholds.yaml, CPA, шаблоны.

Внутренние данные (пороги тиров, CPA-ставки) лежат только в локальных файлах
`criteria.yaml` и `cpa_by_country.csv` — они в .gitignore. В репозитории есть
только `*.example.*` с вымышленными числами; если реальных файлов нет,
квалификатор работает по примерам и громко об этом предупреждает.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

try:  # python-dotenv необязателен, если переменные уже в окружении
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


DEFAULT_CONFIG_DIR = "config/qualifier"
DEFAULT_TEMPLATES_DIR = "templates"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Метрики, которые можно использовать как условия тира в criteria.yaml
TIER_METRICS = ("followers_single_platform", "community_members")


class ConfigError(ValueError):
    """Ошибка в конфиге — текст показывается как есть."""


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    """Значения из .env (раздел 12 ТЗ)."""

    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session_name: str = "qualifier"
    tg_session_dir: str = "sessions"
    tg_posts_limit: int = 50
    rate_min_interval: float = 2.0
    rate_max_interval: float = 3.0
    floodwait_abort_streak: int = 2

    yt_api_key: str = ""
    yt_videos_limit: int = 10
    x_bearer_token: str = ""

    anthropic_api_key: str = ""
    llm_model: str = DEFAULT_MODEL
    llm_effort: str = "medium"
    llm_thinking: str = "adaptive"
    llm_max_tokens: int = 16000

    cache_ttl_days: int = 7
    max_channels_per_run: int = 50
    batch_pause_sec: float = 3.0

    db_path: str = "qualifier.db"
    config_dir: str = DEFAULT_CONFIG_DIR
    templates_dir: str = DEFAULT_TEMPLATES_DIR
    reports_dir: str = "reports"
    draft_language: str = "en"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash)

    @property
    def session_file(self) -> Path:
        return Path(self.tg_session_dir) / f"{self.tg_session_name}.session"

    @property
    def llm_configured(self) -> bool:
        # SDK умеет брать ключ и из профиля `ant auth login`, но для явности
        # квалификатор требует ANTHROPIC_API_KEY (или ANTHROPIC_AUTH_TOKEN)
        return bool(self.anthropic_api_key or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def load_settings(env_file: Optional[str] = None) -> Settings:
    load_dotenv(env_file or ".env", override=False)
    return Settings(
        tg_api_id=_env_int("TG_API_ID", 0),
        tg_api_hash=_env("TG_API_HASH"),
        tg_session_name=_env("TG_SESSION_NAME", "qualifier"),
        tg_session_dir=_env("TG_SESSION_DIR", "sessions"),
        tg_posts_limit=_env_int("TG_POSTS_LIMIT", 50),
        rate_min_interval=_env_float("RATE_MIN_INTERVAL", 2.0),
        rate_max_interval=_env_float("RATE_MAX_INTERVAL", 3.0),
        floodwait_abort_streak=_env_int("FLOODWAIT_ABORT_STREAK", 2),
        yt_api_key=_env("YT_API_KEY"),
        yt_videos_limit=_env_int("YT_VIDEOS_LIMIT", 10),
        x_bearer_token=_env("X_BEARER_TOKEN"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        llm_model=_env("LLM_MODEL", DEFAULT_MODEL),
        llm_effort=_env("LLM_EFFORT", "medium").lower(),
        llm_thinking=_env("LLM_THINKING", "adaptive").lower(),
        llm_max_tokens=_env_int("LLM_MAX_TOKENS", 16000),
        cache_ttl_days=_env_int("CACHE_TTL_DAYS", 7),
        max_channels_per_run=_env_int("MAX_CHANNELS_PER_RUN", 50),
        batch_pause_sec=_env_float("BATCH_PAUSE_SEC", 3.0),
        db_path=_env("QUALIFIER_DB_PATH", "qualifier.db"),
        config_dir=_env("QUALIFIER_CONFIG_DIR", DEFAULT_CONFIG_DIR),
        templates_dir=_env("QUALIFIER_TEMPLATES_DIR", DEFAULT_TEMPLATES_DIR),
        reports_dir=_env("REPORTS_DIR", "reports"),
        draft_language=_env("DRAFT_LANGUAGE", "en").lower(),
    )


# --------------------------------------------------------------------------
# criteria.yaml
# --------------------------------------------------------------------------

@dataclass
class Tier:
    rate: float
    conditions: dict[str, int]


@dataclass
class AffiliateType:
    name: str
    tiers: list[Tier]           # отсортированы по убыванию ставки
    match: str = "any"          # any — достаточно одного условия, all — нужны все


@dataclass
class Criteria:
    affiliate_types: dict[str, AffiliateType]
    cpa_rates_file: str = "cpa_by_country.csv"
    borderline_margin: float = 0.10
    test_period_days: int = 30
    review_metric: str = "share of registered users who started trading, and trading volume"
    path: Optional[Path] = None
    is_example: bool = False


def _resolve(config_dir: Path, name: str) -> tuple[Optional[Path], bool]:
    """Реальный файл или его пример. Возвращает (путь, это_пример)."""
    real = config_dir / name
    if real.exists():
        return real, False
    stem, dot, ext = name.rpartition(".")
    example = config_dir / f"{stem}.example.{ext}" if dot else config_dir / f"{name}.example"
    if example.exists():
        return example, True
    return None, False


def load_criteria(config_dir: str | Path) -> Criteria:
    config_dir = Path(config_dir)
    path, is_example = _resolve(config_dir, "criteria.yaml")
    if path is None:
        raise ConfigError(f"Нет {config_dir / 'criteria.yaml'} (и примера criteria.example.yaml)")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_types = data.get("affiliate_types") or {}
    if not raw_types:
        raise ConfigError(f"{path}: нет секции affiliate_types")

    types: dict[str, AffiliateType] = {}
    for name, raw in raw_types.items():
        raw = raw or {}
        match = str(raw.get("match", "any")).lower()
        if match not in ("any", "all"):
            raise ConfigError(f"{path}: {name}.match должен быть any или all")
        tiers: list[Tier] = []
        for item in raw.get("tiers") or []:
            if not isinstance(item, dict) or "rate" not in item:
                raise ConfigError(f"{path}: у тира {name} нет rate: {item!r}")
            conditions = {k: v for k, v in item.items() if k != "rate"}
            unknown = set(conditions) - set(TIER_METRICS)
            if unknown:
                raise ConfigError(
                    f"{path}: неизвестные условия тира {name}: {sorted(unknown)}. "
                    f"Доступны: {', '.join(TIER_METRICS)}"
                )
            if not conditions:
                raise ConfigError(f"{path}: у тира {name} rate={item['rate']} нет условий")
            try:
                tiers.append(
                    Tier(rate=float(item["rate"]),
                         conditions={k: int(v) for k, v in conditions.items()})
                )
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{path}: тир {name}: {exc}") from exc
        if not tiers:
            raise ConfigError(f"{path}: у типа {name} нет тиров")
        tiers.sort(key=lambda t: t.rate, reverse=True)
        types[str(name)] = AffiliateType(name=str(name), tiers=tiers, match=match)

    deal = data.get("deal") or {}
    return Criteria(
        affiliate_types=types,
        cpa_rates_file=str(data.get("cpa_rates_file") or "cpa_by_country.csv"),
        borderline_margin=float(data.get("borderline_margin", 0.10)),
        test_period_days=int(deal.get("test_period_days", 30)),
        review_metric=str(deal.get("review_metric") or Criteria.review_metric),
        path=path,
        is_example=is_example,
    )


# --------------------------------------------------------------------------
# cpa_by_country.csv
# --------------------------------------------------------------------------

@dataclass
class CpaRate:
    country: str          # ISO2 или * (ставка по умолчанию для региона)
    region: str
    rate: float
    currency: str = "USD"
    event: str = "FTT"


@dataclass
class CpaTable:
    rates: list[CpaRate] = field(default_factory=list)
    path: Optional[Path] = None
    is_example: bool = False

    def lookup(self, country: Optional[str], region: Optional[str]) -> tuple[Optional[CpaRate], str]:
        """Ставка по стране, иначе региональная по умолчанию. -> (ставка, как найдена)."""
        if country:
            for rate in self.rates:
                if rate.country.upper() == country.upper():
                    return rate, "country"
        if region:
            for rate in self.rates:
                if rate.country == "*" and rate.region.lower() == region.lower():
                    return rate, "region"
        return None, ""


def load_cpa(config_dir: str | Path, filename: str = "cpa_by_country.csv") -> CpaTable:
    config_dir = Path(config_dir)
    path, is_example = _resolve(config_dir, filename)
    if path is None:
        return CpaTable()
    rates: list[CpaRate] = []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = (line for line in fh if line.strip() and not line.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            country = row.get("country_code") or row.get("country") or ""
            raw_rate = row.get("cpa") or row.get("cpa_usd") or row.get("rate") or ""
            if not country or not raw_rate:
                continue
            try:
                value = float(raw_rate.replace(",", ".").lstrip("$"))
            except ValueError as exc:
                raise ConfigError(f"{path}: некорректная ставка {raw_rate!r} для {country}") from exc
            rates.append(
                CpaRate(
                    country=country.upper() if country != "*" else "*",
                    region=row.get("region", ""),
                    rate=value,
                    currency=(row.get("currency") or "USD").upper(),
                    event=row.get("event") or "FTT",
                )
            )
    return CpaTable(rates=rates, path=path, is_example=is_example)


# --------------------------------------------------------------------------
# thresholds.yaml
# --------------------------------------------------------------------------

_THRESHOLD_DEFAULTS: dict[str, Any] = {
    "platforms": {
        "telegram": {
            "views_window": 20,
            "views_min_age_hours": 24,
            "er_min": [
                {"max_followers": 10000, "min_er": 10.0},
                {"max_followers": None, "min_er": 5.0},
            ],
        },
        "youtube": {
            "views_window": 10,
            "views_min_age_hours": 72,
            "er_min": [
                {"max_followers": 10000, "min_er": 4.0},
                {"max_followers": None, "min_er": 1.0},
            ],
        },
        "x": {
            "views_window": 20,
            "views_min_age_hours": 24,
            "er_min": [
                {"max_followers": 10000, "min_er": 3.0},
                {"max_followers": None, "min_er": 1.0},
            ],
        },
    },
    "frequency_window_days": 30,
    "ads_window_posts": 30,
    "views_spike": {"min_sample": 5, "cv_max": 1.0, "max_to_median": 5.0},
    "dead_subs": {
        "min_followers": 3000,
        "max_er": {"telegram": 3.0, "youtube": 0.5, "x": 0.3},
        "max_avg_reactions": 3.0,
    },
    "no_comments": {"max_avg_reactions": 3.0},
    "inactive": {"max_days_since_last_post": 14},
    "airdrop": {"min_share": 0.4, "keywords": []},
    "brand_risk_markers": [],
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class Thresholds:
    data: dict[str, Any]
    path: Optional[Path] = None

    def platform(self, platform: str) -> dict[str, Any]:
        platforms = self.data.get("platforms") or {}
        return platforms.get(platform) or platforms.get("telegram") or {}

    def min_er(self, platform: str, followers: int) -> Optional[float]:
        for bucket in self.platform(platform).get("er_min") or []:
            limit = bucket.get("max_followers")
            if limit is None or followers <= int(limit):
                return float(bucket.get("min_er"))
        return None

    def per_platform(self, value: Any, platform: str) -> Optional[float]:
        """Порог может быть числом или словарём {площадка: число}."""
        if isinstance(value, dict):
            raw = value.get(platform)
            return None if raw is None else float(raw)
        return None if value is None else float(value)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def load_thresholds(config_dir: str | Path) -> Thresholds:
    config_dir = Path(config_dir)
    path, _ = _resolve(config_dir, "thresholds.yaml")
    if path is None:
        return Thresholds(data=dict(_THRESHOLD_DEFAULTS))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Thresholds(data=_merge(_THRESHOLD_DEFAULTS, raw), path=path)


# --------------------------------------------------------------------------
# geo_markers.yaml
# --------------------------------------------------------------------------

@dataclass
class CountryProfile:
    code: str
    name: str
    name_en: str = ""
    region: str = ""
    languages: dict[str, float] = field(default_factory=dict)
    markers: list[str] = field(default_factory=list)


def load_geo_profiles(config_dir: str | Path) -> dict[str, CountryProfile]:
    path = Path(config_dir) / "geo_markers.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles: dict[str, CountryProfile] = {}
    for code, raw in (data.get("countries") or {}).items():
        raw = raw or {}
        code = str(code).upper()
        profiles[code] = CountryProfile(
            code=code,
            name=str(raw.get("name") or code),
            name_en=str(raw.get("name_en") or raw.get("name") or code),
            region=str(raw.get("region") or ""),
            languages={str(k): float(v) for k, v in (raw.get("languages") or {}).items()},
            markers=[str(m) for m in raw.get("markers") or []],
        )
    return profiles


# --------------------------------------------------------------------------
# Всё вместе
# --------------------------------------------------------------------------

@dataclass
class QualifierConfig:
    criteria: Criteria
    cpa: CpaTable
    thresholds: Thresholds
    geo: dict[str, CountryProfile]
    templates_dir: Path

    def template(self, name: str, language: Optional[str] = None) -> str:
        """Шаблон; для языка ищется вариант name.<lang>.md, иначе базовый."""
        candidates = []
        if language:
            stem, dot, ext = name.rpartition(".")
            candidates.append(self.templates_dir / f"{stem}.{language}.{ext}")
        candidates.append(self.templates_dir / name)
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        raise ConfigError(f"Нет шаблона {candidates[-1]}")


def load_config(settings: Settings) -> QualifierConfig:
    criteria = load_criteria(settings.config_dir)
    return QualifierConfig(
        criteria=criteria,
        cpa=load_cpa(settings.config_dir, criteria.cpa_rates_file),
        thresholds=load_thresholds(settings.config_dir),
        geo=load_geo_profiles(settings.config_dir),
        templates_dir=Path(settings.templates_dir),
    )
