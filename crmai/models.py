"""Структуры данных анализатора: сообщение, статус сделки, замечание, алерт."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

DIRECTION_IN = "in"
DIRECTION_OUT = "out"

SEVERITY_CRITICAL = "critical"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"

SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_MAJOR: 1, SEVERITY_MINOR: 2}

# Метка для голосовых и файлов: содержимое не расшифровываем (MVP, раздел 3.1 ТЗ)
MEDIA_MARKER = "[media]"

STAGES = (
    "холодный заход",
    "переговоры об условиях",
    "согласование контента",
    "оплачено, ждём выход",
    "контент вышел, контроль",
    "конфликт",
    "слив",
    "неактивен",
)

TEMPERATURES = ("hot", "warm", "cold")


@dataclass
class Message:
    """Нормализованное событие Wazzup."""

    message_id: str
    chat_id: str
    direction: str
    text: str
    ts: str
    chat_type: str = ""
    contact_name: str = ""
    contact_username: str = ""
    contact_phone: str = ""
    amo_contact_id: Optional[int] = None
    amo_lead_id: Optional[int] = None
    is_media: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_outgoing(self) -> bool:
        return self.direction == DIRECTION_OUT

    @property
    def role(self) -> str:
        """OUT — наш менеджер, IN — партнёр (формат диалога для промпта П1)."""
        return "OUT" if self.is_outgoing else "IN"


@dataclass
class RedFlag:
    """Срабатывание regex-слоя мгновенных алертов (раздел 4.2 ТЗ)."""

    rule: str
    title: str
    quote: str
    note: str = ""


@dataclass
class StatusAnalysis:
    """Разобранный ответ LLM по промпту П1."""

    partner: str = ""
    stage: str = ""
    summary: str = ""
    agreed: list[str] = field(default_factory=list)
    waiting_for: str = ""
    next_step: str = ""
    next_step_date: Optional[str] = None
    risk: Optional[str] = None
    temperature: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "StatusAnalysis":
        agreed = data.get("agreed") or []
        if isinstance(agreed, str):
            agreed = [agreed]
        return cls(
            partner=_text(data.get("partner")),
            stage=_text(data.get("stage")),
            summary=_text(data.get("summary")),
            agreed=[_text(item) for item in agreed if _text(item)],
            waiting_for=_text(data.get("waiting_for")),
            next_step=_text(data.get("next_step")),
            next_step_date=_optional(data.get("next_step_date")),
            risk=_optional(data.get("risk")),
            temperature=_text(data.get("temperature")).lower(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "partner": self.partner,
            "stage": self.stage,
            "summary": self.summary,
            "agreed": self.agreed,
            "waiting_for": self.waiting_for,
            "next_step": self.next_step,
            "next_step_date": self.next_step_date,
            "risk": self.risk,
            "temperature": self.temperature,
        }

    @property
    def is_hot(self) -> bool:
        return self.temperature == "hot"

    def next_step_overdue(self, today: datetime | None = None) -> bool:
        """Нет даты следующего шага или она в прошлом — сделка зависла."""
        if not self.next_step_date:
            return True
        parsed = parse_date(self.next_step_date)
        if parsed is None:
            return True
        reference = (today or datetime.now()).date()
        return parsed < reference


@dataclass
class QualityVerdict:
    """Одно замечание из промпта П2."""

    chat: str = ""
    quote: str = ""
    issue: str = ""
    severity: str = SEVERITY_MINOR
    suggestion: str = ""
    chat_id: str = ""
    lead_id: Optional[int] = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "QualityVerdict":
        severity = _text(data.get("severity")).lower()
        if severity not in SEVERITY_ORDER:
            severity = SEVERITY_MINOR
        return cls(
            chat=_text(data.get("chat")),
            quote=_text(data.get("quote"))[:200],
            issue=_text(data.get("issue")),
            severity=severity,
            suggestion=_text(data.get("suggestion")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat": self.chat,
            "quote": self.quote,
            "issue": self.issue,
            "severity": self.severity,
            "suggestion": self.suggestion,
            "chat_id": self.chat_id,
            "lead_id": self.lead_id,
        }

    @property
    def is_critical(self) -> bool:
        return self.severity == SEVERITY_CRITICAL


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional(value: Any) -> Optional[str]:
    text = _text(value)
    if not text or text.lower() in ("null", "none", "-", "нет"):
        return None
    return text


def parse_date(value: str) -> Optional[date]:
    """`YYYY-MM-DD` -> date. Всё остальное -> None (LLM иногда фантазирует формат)."""
    text = _text(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
