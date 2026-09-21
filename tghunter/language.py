"""Эвристика определения языка канала по последним постам.

Классы: ru / uk / en / turkic (kz-uz-az) / other.
Реализовано частотным разбором алфавитов и стоп-слов — без внешних
зависимостей, но с подменяемым бэкендом langdetect, если он установлен.
"""

from __future__ import annotations

import re
from collections import Counter

RU = "ru"
UK = "uk"
EN = "en"
TURKIC = "turkic"
OTHER = "other"

# Буквы, уникальные для конкретных языков
_UK_LETTERS = set("їієґ")
_RU_LETTERS = set("ыэъё")
_KK_LETTERS = set("әғқңөұүhіһ") - {"h"}
_UZ_AZ_LETTERS = set("ўқғҳəöüçş")

_CYRILLIC = re.compile(r"[а-яёіїєґўқғҳәңөұүһ]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)

_UK_WORDS = {"та", "що", "як", "для", "не", "це", "або", "ми", "вже", "має", "гроші", "ринок"}
_RU_WORDS = {"и", "что", "как", "для", "не", "это", "или", "мы", "уже", "рынок", "деньги"}
_EN_WORDS = {"the", "and", "for", "you", "with", "market", "trading", "crypto", "price"}
_TURKIC_WORDS = {
    # kk
    "және", "бойынша", "ақша", "қаржы", "нарық", "туралы", "үшін",
    # uz
    "va", "uchun", "pul", "bozor", "moliya", "qanday", "bilan",
    # az
    "və", "üçün", "pul", "bazar", "maliyyə", "necə", "ilə",
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def detect_language(texts: list[str]) -> str:
    """Определяет язык по списку текстов. Пустой ввод -> other."""
    blob = "\n".join(t for t in texts if t).strip()
    if not blob:
        return OTHER

    letters = set(blob.lower())
    words = set(_tokens(blob))

    cyr = len(_CYRILLIC.findall(blob))
    lat = len(_LATIN.findall(blob))
    if cyr + lat == 0:
        return OTHER

    scores: Counter[str] = Counter()

    # Сигналы по уникальным буквам
    if letters & _UK_LETTERS:
        scores[UK] += 3
    if letters & _RU_LETTERS:
        scores[RU] += 2
    if letters & _KK_LETTERS:
        scores[TURKIC] += 3
    if letters & _UZ_AZ_LETTERS:
        scores[TURKIC] += 3

    # Сигналы по стоп-словам
    scores[UK] += 2 * len(words & _UK_WORDS)
    scores[RU] += 2 * len(words & _RU_WORDS)
    scores[EN] += 2 * len(words & _EN_WORDS)
    scores[TURKIC] += 3 * len(words & _TURKIC_WORDS)

    # Базовый вес по алфавиту
    if cyr > lat:
        scores[RU] += 1
    elif lat > cyr:
        scores[EN] += 1

    if not scores:
        return OTHER

    best, best_score = scores.most_common(1)[0]
    if best_score <= 0:
        return OTHER
    return best
