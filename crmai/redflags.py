"""Мгновенные алерты без LLM: стоп-паттерны в исходящих (раздел 4.2 ТЗ).

Каждое исходящее сообщение проверяется regex-слоем сразу при приёме вебхука —
до всяких батчей. Совпадение уходит руководителю в Telegram с цитатой и
ссылкой на сделку.

Слой намеренно грубый и быстрый: он ловит формулировки-обязательства и
запрещённые цифры. Тонкие случаи (тон, возражения, отсутствие дат) ловит
уровень 2 через LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Pattern

from .models import Message, RedFlag

# На сколько символов друг от друга должны стоять два паттерна, чтобы считаться
# «рядом»: примерно одно предложение
PROXIMITY_WINDOW = 80
# Сколько символов исходного текста показываем в цитате вокруг совпадения
QUOTE_WIDTH = 90


def normalize(text: str) -> str:
    """Регистронезависимо и без ё. Длина сохраняется — позиции совпадают с оригиналом."""
    return (text or "").lower().replace("ё", "е")


@dataclass
class Rule:
    """Стоп-паттерн: одиночный или пара «рядом стоящих» выражений."""

    name: str
    title: str
    pattern: Pattern[str]
    near: Optional[Pattern[str]] = None
    note: str = ""
    window: int = PROXIMITY_WINDOW

    def find(self, text: str) -> Optional[tuple[int, int]]:
        """Границы первого совпадения в нормализованном тексте или None."""
        for match in self.pattern.finditer(text):
            if self.near is None:
                return match.span()
            for other in self.near.finditer(text):
                if _distance(match.span(), other.span()) <= self.window:
                    start = min(match.start(), other.start())
                    end = max(match.end(), other.end())
                    return start, end
        return None


def _distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Расстояние между двумя участками текста (0, если пересекаются)."""
    if a[0] <= b[1] and b[0] <= a[1]:
        return 0
    return b[0] - a[1] if b[0] > a[1] else a[0] - b[1]


def _re(pattern: str) -> Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


# Порядок важен: первым в алерт попадает самое серьёзное правило
RULES: tuple[Rule, ...] = (
    Rule(
        name="guaranteed_income",
        title="Обещание гарантированной доходности",
        pattern=_re(r"гарантир\w*"),
        near=_re(r"доход\w*|прибыл\w*|процент\w*|%"),
    ),
    Rule(
        name="sure_earnings",
        title="Обещание заработка («точно заработаете»)",
        pattern=_re(r"точно\s+(?:\w+\s+){0,2}?зараб\w*"),
    ),
    Rule(
        name="no_risk",
        title="Обещание отсутствия рисков",
        pattern=_re(r"без\s+(?:всяк\w*\s+|каких[- ]либо\s+)?риск\w*"),
    ),
    Rule(
        name="passive_income",
        title="Формулировка «пассивный доход»",
        pattern=_re(r"пассивн\w*\s+доход\w*"),
    ),
    Rule(
        name="ref_rate",
        title="Названа реферальная ставка выше согласованной",
        pattern=_re(r"(?<!\d)(?:35|40|45|50)\s*%"),
        near=_re(r"рефк\w*|реферал\w*|ставк\w*|отчислен\w*"),
    ),
    Rule(
        name="payment_split",
        title="Озвучена внутренняя схема оплаты 50/50",
        pattern=_re(r"50\s*/\s*50|пополам"),
        near=_re(r"оплат\w*|плат\w*|аванс\w*"),
    ),
    Rule(
        name="fix_amount",
        title="Названа сумма фикса",
        pattern=_re(r"\$\s?\d[\d\s.,]*|\d[\d\s.,]*\s?(?:\$|usd|долл\w*)"),
        near=_re(r"фикс\w*"),
        note="проверь, согласована ли сумма",
    ),
    Rule(
        name="branded_bot",
        title="Обещание брендированного/персонального бота",
        pattern=_re(r"брендирован\w*|персональн\w*\s+бот\w*"),
    ),
    Rule(
        name="pressure",
        title="Давление, угрозы, упоминание судов и связей",
        pattern=_re(
            r"верни\w*\s+деньг\w*|пойду\s+к\s|у\s+нас\s+связи|юрист\w*"
            r"|\bсуд(?:\b|ом\b|а\b|е\b|ы\b|ебн\w*)"
        ),
    ),
)

RULES_BY_NAME = {rule.name: rule for rule in RULES}


def quote_around(text: str, start: int, end: int, width: int = QUOTE_WIDTH) -> str:
    """Фрагмент исходного текста вокруг совпадения — идёт в алерт."""
    if not text:
        return ""
    left = max(0, start - width // 2)
    right = min(len(text), end + width // 2)
    fragment = " ".join(text[left:right].split())
    if left > 0:
        fragment = "…" + fragment
    if right < len(text):
        fragment = fragment + "…"
    return fragment


def scan_text(text: str, rules: Iterable[Rule] = RULES) -> list[RedFlag]:
    """Все сработавшие стоп-паттерны в тексте."""
    if not text or not text.strip():
        return []
    normalized = normalize(text)
    flags: list[RedFlag] = []
    for rule in rules:
        span = rule.find(normalized)
        if span is None:
            continue
        flags.append(
            RedFlag(
                rule=rule.name,
                title=rule.title,
                quote=quote_around(text, span[0], span[1]),
                note=rule.note,
            )
        )
    return flags


def scan_message(message: Message) -> list[RedFlag]:
    """Проверяем только исходящие: чек-лист — про сообщения менеджера."""
    if not message.is_outgoing:
        return []
    return scan_text(message.text)


def describe_rules() -> str:
    """Список правил для команды `rules` в CLI."""
    lines = []
    for rule in RULES:
        suffix = f" (рядом: {rule.near.pattern})" if rule.near else ""
        lines.append(f"{rule.name:<18} {rule.title}\n{'':<19}{rule.pattern.pattern}{suffix}")
    return "\n".join(lines)


__all__ = [
    "RULES",
    "RULES_BY_NAME",
    "Rule",
    "describe_rules",
    "normalize",
    "quote_around",
    "scan_message",
    "scan_text",
]


# Тип для внешних расширений: любой вызываемый, который отдаёт список флагов
Scanner = Callable[[str], list[RedFlag]]
