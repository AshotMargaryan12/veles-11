"""Шаблоны промптов и сборка контекста диалога (раздел 5 ТЗ).

Промпты лежат текстовыми файлами в prompts/ — их правит владелец системы без
похода в код. Содержимое prompts/company_context.md подставляется в конец
обоих системных промптов (раздел 5.1 ТЗ).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .config import MAX_DIALOG_MESSAGES
from .models import DIRECTION_OUT

log = logging.getLogger(__name__)

P1_SYSTEM = "p1_status_system.md"
P1_USER = "p1_status_user.md"
P2_SYSTEM = "p2_quality_system.md"
P2_USER = "p2_quality_user.md"
COMPANY_CONTEXT = "company_context.md"

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class PromptError(RuntimeError):
    """Не нашли или не смогли прочитать файл промпта."""


@dataclass
class Prompts:
    """Загруженные шаблоны. Читаются при каждом батче: правка файла — без рестарта."""

    directory: Path
    p1_system: str
    p1_user: str
    p2_system: str
    p2_user: str
    company_context: str = ""

    @classmethod
    def load(cls, directory: str | Path = "prompts") -> "Prompts":
        path = Path(directory)
        if not path.exists():
            raise PromptError(f"Не найден каталог промптов: {path}")
        context = _read_optional(path / COMPANY_CONTEXT)
        return cls(
            directory=path,
            p1_system=_read(path / P1_SYSTEM),
            p1_user=_read(path / P1_USER),
            p2_system=_read(path / P2_SYSTEM),
            p2_user=_read(path / P2_USER),
            company_context=strip_comments(context),
        )

    def status_system(self) -> str:
        return with_context(self.p1_system, self.company_context)

    def quality_system(self) -> str:
        return with_context(self.p2_system, self.company_context)

    def status_user(self, prev_summary: str, dialog: str) -> str:
        return self.p1_user.format(prev_summary=prev_summary or "пусто", dialog=dialog)

    def quality_user(self, date: str, dialogs: str) -> str:
        return self.p2_user.format(date=date, dialogs=dialogs)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptError(f"Не читается файл промпта {path}: {exc}") from exc


def _read_optional(path: Path) -> str:
    if not path.exists():
        log.info("Файл контекста компании %s не найден — промпты идут без него", path)
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — права на файл
        log.warning("Не читается контекст компании %s: %s", path, exc)
        return ""


def strip_comments(text: str) -> str:
    """Убирает html-комментарии: они нужны владельцу файла, а не модели."""
    return _HTML_COMMENT_RE.sub("", text or "").strip()


def with_context(system_prompt: str, company_context: str) -> str:
    """Подставляет контекст компании в конец системного промпта (раздел 5.1 ТЗ)."""
    if not company_context.strip():
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        "---\n"
        "ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ КОМПАНИИ (внутренние правила и памятка по возражениям):\n\n"
        f"{company_context.strip()}"
    )


# --- сборка диалога ---------------------------------------------------------


def _row_value(row: Any, key: str, default: str = "") -> str:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = getattr(row, key, default)
    return "" if value is None else str(value)


def format_ts(ts: str) -> str:
    """ISO-время -> `21.09 14:05` для читаемости промпта."""
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:16]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.strftime("%d.%m %H:%M")


def format_dialog(rows: Sequence[Any], limit: int = MAX_DIALOG_MESSAGES) -> str:
    """Сообщения -> `[дата] РОЛЬ: текст` (формат из юзер-промпта П1).

    Если сообщений больше лимита, берём последние: свежий контекст важнее,
    а старое состояние приходит отдельно через prev_summary.
    """
    tail = list(rows)[-limit:] if limit else list(rows)
    lines = []
    for row in tail:
        role = "OUT" if _row_value(row, "direction") == DIRECTION_OUT else "IN"
        text = " ".join(_row_value(row, "text").split()) or "[пусто]"
        lines.append(f"[{format_ts(_row_value(row, 'ts'))}] {role}: {text}")
    return "\n".join(lines)


def format_prev_summary(summary: Optional[dict[str, Any]]) -> str:
    """Предыдущий статус сделки в компактном виде для юзер-промпта П1."""
    if not summary:
        return "пусто"
    parts = [
        f"этап: {summary.get('stage') or '-'}",
        f"температура: {summary.get('temperature') or '-'}",
        f"суть: {summary.get('summary') or '-'}",
    ]
    agreed = summary.get("agreed") or []
    if agreed:
        parts.append("зафиксировано: " + "; ".join(str(item) for item in agreed))
    if summary.get("waiting_for"):
        parts.append(f"ждём: {summary['waiting_for']}")
    if summary.get("next_step"):
        step = summary["next_step"]
        if summary.get("next_step_date"):
            step = f"{step} (до {summary['next_step_date']})"
        parts.append(f"след. шаг: {step}")
    if summary.get("risk"):
        parts.append(f"риск: {summary['risk']}")
    return "; ".join(parts)


def format_quality_block(
    partner: str, context_rows: Iterable[Any], outgoing_rows: Iterable[Any]
) -> str:
    """Один диалог для промпта П2: контекст + исходящие за день."""
    lines = [f"### Диалог: {partner}"]
    context = list(context_rows)
    if context:
        lines.append("Контекст (последние сообщения до начала дня):")
        lines.append(format_dialog(context))
    lines.append("Исходящие менеджера за день:")
    outgoing = list(outgoing_rows)
    lines.append(format_dialog(outgoing) if outgoing else "(нет)")
    return "\n".join(lines)
