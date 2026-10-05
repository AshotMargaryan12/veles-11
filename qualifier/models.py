"""Структуры данных квалификатора. Без сети и без внешних библиотек.

Сборщики площадок (`qualifier.platforms`) отдают `PlatformData`, дальше вся
логика — метрики, гео, флаги, квалификация, карточка — работает только с этими
структурами и поэтому тестируется офлайн.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Optional

PLATFORM_TELEGRAM = "telegram"
PLATFORM_YOUTUBE = "youtube"
PLATFORM_X = "x"

PLATFORM_TITLES = {
    PLATFORM_TELEGRAM: "Telegram",
    PLATFORM_YOUTUBE: "YouTube",
    PLATFORM_X: "X",
}

# Как называется аудитория на площадке (для строки «Площадки: ...»)
FOLLOWERS_WORD = {
    PLATFORM_TELEGRAM: "подписчиков",
    PLATFORM_YOUTUBE: "подписчиков",
    PLATFORM_X: "фолловеров",
}

# Статусы сбора данных по площадке
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"   # доступ ограничен / нет ключа — не падаем
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"

# Вид площадки
KIND_CHANNEL = "channel"     # канал / профиль с подписчиками
KIND_GROUP = "group"         # Telegram-чат (комьюнити)

INSUFFICIENT = "insufficient_data"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _dt_from_str(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class PlatformRef:
    """Разобранная ссылка на площадку."""

    platform: str
    handle: str
    url: str
    raw: str = ""
    # youtube: handle | channel_id | user | custom | video; x/telegram: handle
    kind: str = "handle"

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.kind}:{self.handle.lower()}"

    @property
    def title(self) -> str:
        return PLATFORM_TITLES.get(self.platform, self.platform)


@dataclass
class ContentItem:
    """Пост / видео / твит в нормализованном виде."""

    id: str
    date: Optional[datetime] = None
    text: str = ""
    views: Optional[int] = None
    reactions: Optional[int] = None   # реакции / лайки
    comments: Optional[int] = None
    group_id: Optional[str] = None    # альбом Telegram
    url: Optional[str] = None
    duration_sec: Optional[int] = None  # для видео: отличить Shorts

    def age_days(self, now: datetime) -> Optional[float]:
        if self.date is None:
            return None
        return (now - self.date).total_seconds() / 86400.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["date"] = _dt_to_str(self.date)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentItem":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        clean["date"] = _dt_from_str(clean.get("date"))
        return cls(**clean)


@dataclass
class PlatformData:
    """Сырые публичные данные одной площадки."""

    platform: str
    handle: str
    url: str
    status: str = STATUS_OK
    status_reason: str = ""
    kind: str = KIND_CHANNEL
    title: str = ""
    username: Optional[str] = None
    description: str = ""
    followers: Optional[int] = None
    created_at: Optional[datetime] = None
    country: Optional[str] = None        # страна из профиля (ISO2), если указана
    location: Optional[str] = None       # свободный текст локации (X)
    language_hint: Optional[str] = None  # язык из профиля (YouTube defaultLanguage)
    has_community_chat: Optional[bool] = None
    community_members: Optional[int] = None
    community_title: Optional[str] = None
    items: list[ContentItem] = field(default_factory=list)
    items_limit: Optional[int] = None    # сколько постов запрашивали (для «N+»)
    source: str = ""                     # telethon | youtube_api | yt_dlp | x_api | demo
    approximate: bool = False            # цифры округлены источником (yt-dlp)
    notes: list[str] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=utcnow)
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def platform_title(self) -> str:
        return PLATFORM_TITLES.get(self.platform, self.platform)

    @property
    def display_handle(self) -> str:
        name = self.username or self.handle
        if self.platform == PLATFORM_YOUTUBE and name.startswith("UC"):
            return name
        return name if name.startswith("@") else f"@{name}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        data["created_at"] = _dt_to_str(self.created_at)
        data["fetched_at"] = _dt_to_str(self.fetched_at)
        data.pop("from_cache", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformData":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        clean["items"] = [ContentItem.from_dict(i) for i in clean.get("items") or []]
        clean["created_at"] = _dt_from_str(clean.get("created_at"))
        clean["fetched_at"] = _dt_from_str(clean.get("fetched_at")) or utcnow()
        return cls(**clean)


@dataclass
class PlatformMetrics:
    """Посчитанные метрики площадки (раздел 4 ТЗ)."""

    platform: str
    followers: Optional[int] = None
    views_sample: int = 0
    avg_views: Optional[float] = None
    median_views: Optional[int] = None
    er: Optional[float] = None           # медиана просмотров / подписчики, %
    er_avg: Optional[float] = None       # среднее просмотров / подписчики, %
    views_cv: Optional[float] = None     # коэффициент вариации просмотров
    max_to_median: Optional[float] = None
    young_posts_excluded: int = 0
    shorts_excluded: int = 0
    posts_30d: int = 0
    posts_30d_capped: bool = False       # постов больше, чем выгружено (N+)
    last_post_at: Optional[datetime] = None
    days_since_last_post: Optional[int] = None
    avg_reactions: Optional[float] = None
    avg_comments: Optional[float] = None
    has_community_chat: Optional[bool] = None
    community_members: Optional[int] = None
    ads_posts: int = 0
    ads_window: int = 0
    ads_markers: list[str] = field(default_factory=list)
    competitor_ref_posts: int = 0
    contact: Optional[str] = None
    items_total: int = 0

    @property
    def sells_ads(self) -> bool:
        return self.ads_posts > 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_post_at"] = _dt_to_str(self.last_post_at)
        data["sells_ads"] = self.sells_ads
        return data


@dataclass
class LanguageResult:
    primary: str = "unknown"
    share: float = 0.0
    confidence: str = "low"
    distribution: dict[str, float] = field(default_factory=dict)
    sample_size: int = 0


@dataclass
class GeoSignal:
    country: str
    kind: str        # language | profile | markers | llm | manual
    weight: float
    detail: str


@dataclass
class GeoEstimate:
    country: Optional[str] = None
    country_name: str = "не определено"
    country_name_en: str = "not determined"
    region: Optional[str] = None
    confidence: str = "low"            # high | medium | low | manual
    source: str = "auto"               # auto | manual
    signals: list[GeoSignal] = field(default_factory=list)
    alternatives: list[tuple[str, float]] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return self.source != "manual" and self.confidence != "high"


@dataclass
class Assessed:
    """Одна оценка LLM: значение + обоснование цитатами/наблюдениями."""

    value: str = INSUFFICIENT
    evidence: list[str] = field(default_factory=list)
    note: str = ""            # пояснение кода (например, «нет обоснования»)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return self.value != INSUFFICIENT


@dataclass
class RedFlag:
    type: str
    quote: str
    comment: str = ""
    verified: Optional[bool] = None   # цитата найдена в контенте


@dataclass
class AudienceAssessment:
    """Результат LLM-слоя (раздел 5 ТЗ)."""

    available: bool = False
    error: str = ""
    model: str = ""
    audience_type: Assessed = field(default_factory=Assessed)
    audience_secondary: str = "none"
    conversion: Assessed = field(default_factory=Assessed)
    conversion_reason: str = ""
    topics: Assessed = field(default_factory=Assessed)   # value = "a,b,c"
    red_flags_status: str = INSUFFICIENT
    red_flags: list[RedFlag] = field(default_factory=list)
    tone: Assessed = field(default_factory=Assessed)
    brand_fit: str = INSUFFICIENT
    airdrop_share: Assessed = field(default_factory=Assessed)
    geo_hint: Assessed = field(default_factory=Assessed)
    quotes_total: int = 0
    quotes_verified: int = 0
    from_cache: bool = False

    @property
    def topic_list(self) -> list[str]:
        if not self.topics.known or not self.topics.value:
            return []
        return [t for t in self.topics.value.split(",") if t]


@dataclass
class Flag:
    code: str
    explanation: str
    evidence: str = ""
    platform: Optional[str] = None
    severity: str = "warn"     # warn | critical
    source: str = "metrics"    # metrics | llm | keywords


@dataclass
class TierGap:
    metric: str
    have: int
    need: int

    @property
    def missing(self) -> int:
        return max(0, self.need - self.have)


@dataclass
class QualificationResult:
    affiliate_type: str = "individual"
    tier_rate: Optional[float] = None
    tier_met_by: list[str] = field(default_factory=list)
    tier_thresholds: dict[str, int] = field(default_factory=dict)
    next_tier_rate: Optional[float] = None
    next_tier_gaps: list[TierGap] = field(default_factory=list)
    lowest_tier_rate: Optional[float] = None
    followers_single_platform: int = 0
    followers_platform: Optional[str] = None
    community_members: int = 0
    community_source: str = ""
    cpa: Optional[float] = None
    cpa_currency: str = "USD"
    cpa_event: str = ""
    cpa_matched_by: str = ""
    manual_review: bool = False
    manual_review_reasons: list[str] = field(default_factory=list)
    criteria_are_examples: bool = False


@dataclass
class DealProposal:
    """Предлагаемая структура сделки — считается кодом локально."""

    rate: Optional[float] = None
    cpa_included: bool = False
    fixed_fee: bool = False
    test_period_days: int = 30
    review_metric: str = ""
    submit: bool = True          # подавать ли на повышение сейчас


@dataclass
class PartnerRequest:
    """Вход: одна строка — один партнёр (одна или несколько ссылок)."""

    links: list[str]
    geo: Optional[str] = None
    affiliate_type: str = "individual"
    notes: str = ""
    refresh: bool = False


@dataclass
class Card:
    """Карточка квалификации (раздел 8 ТЗ)."""

    partner_name: str
    partner_handle: str
    request: PartnerRequest
    platforms: list[PlatformData]
    metrics: dict[str, PlatformMetrics]
    primary_key: Optional[str]
    language: LanguageResult
    geo: GeoEstimate
    audience: AudienceAssessment
    flags: list[Flag]
    qualification: QualificationResult
    deal: DealProposal
    recommendation: list[str]
    draft: str
    draft_source: str = "local"   # llm | local
    warnings: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)
    report_path: Optional[str] = None

    @property
    def primary(self) -> Optional[PlatformData]:
        for data in self.platforms:
            if platform_key(data) == self.primary_key:
                return data
        return None

    @property
    def primary_metrics(self) -> Optional[PlatformMetrics]:
        if self.primary_key is None:
            return None
        return self.metrics.get(self.primary_key)


def platform_key(data: PlatformData) -> str:
    return f"{data.platform}:{data.handle.lower()}"
