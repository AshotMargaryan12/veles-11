"""Определение языка контента — основной сигнал гео (раздел 4.4 ТЗ).

Без внешних зависимостей: сначала письменность (кириллица, арабская, деванагари
и т. д.), внутри неё — характерные буквы и стоп-слова. Каждый пост
классифицируется отдельно, итог — доли языков по постам.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

from .models import LanguageResult

LANGUAGE_NAMES = {
    "ru": "русский", "uk": "украинский", "be": "белорусский", "kk": "казахский",
    "uz": "узбекский", "az": "азербайджанский", "ky": "киргизский", "tg": "таджикский",
    "ar": "арабский", "fa": "персидский", "ur": "урду", "tr": "турецкий",
    "en": "английский", "es": "испанский", "pt": "португальский", "fr": "французский",
    "de": "немецкий", "it": "итальянский", "pl": "польский", "id": "индонезийский",
    "vi": "вьетнамский", "th": "тайский", "ko": "корейский", "ja": "японский",
    "zh": "китайский", "hi": "хинди", "bn": "бенгальский", "he": "иврит",
    "ka": "грузинский", "hy": "армянский", "el": "греческий", "unknown": "не определён",
}

_SCRIPTS: list[tuple[str, re.Pattern[str]]] = [
    ("cyrillic", re.compile(r"[Ѐ-ӿ]")),
    ("arabic", re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")),
    ("latin", re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]")),
    ("devanagari", re.compile(r"[ऀ-ॿ]")),
    ("bengali", re.compile(r"[ঀ-৿]")),
    ("thai", re.compile(r"[฀-๿]")),
    ("hangul", re.compile(r"[가-힯ᄀ-ᇿ]")),
    ("kana", re.compile(r"[぀-ヿ]")),
    ("han", re.compile(r"[一-鿿]")),
    ("hebrew", re.compile(r"[֐-׿]")),
    ("greek", re.compile(r"[Ͱ-Ͽ]")),
    ("georgian", re.compile(r"[Ⴀ-ჿ]")),
    ("armenian", re.compile(r"[԰-֏]")),
]

_SCRIPT_TO_LANG = {
    "devanagari": "hi", "bengali": "bn", "thai": "th", "hangul": "ko",
    "hebrew": "he", "greek": "el", "georgian": "ka", "armenian": "hy",
}

_WORD_RE = re.compile(r"[^\W\d_]+(?:['ʻ’][^\W\d_]+)?", re.UNICODE)

# --- кириллица -------------------------------------------------------------
_CYR_LETTERS = {
    "uk": set("їєґ"),
    "be": set("ў"),
    "kk": set("әғқңөұүһ"),
    "uz": set("ўқғҳ"),
}
_CYR_WORDS = {
    "ru": {"и", "что", "как", "для", "не", "это", "или", "мы", "уже", "рынок", "деньги",
           "на", "по", "все", "так", "его", "только", "если", "есть", "будет", "сейчас"},
    "uk": {"та", "що", "як", "для", "це", "або", "ми", "вже", "має", "гроші", "ринок",
           "і", "й", "на", "буде", "зараз", "якщо", "тільки", "його", "дуже"},
    "be": {"і", "што", "як", "для", "гэта", "або", "мы", "ўжо", "грошы", "рынак", "калі"},
    "kk": {"және", "бойынша", "ақша", "қаржы", "нарық", "туралы", "үшін", "бұл", "мен"},
    "uz": {"ва", "учун", "пул", "бозор", "билан", "қандай", "бу"},
    "ky": {"жана", "үчүн", "акча", "базар", "менен", "бул"},
    "tg": {"ва", "барои", "пул", "бозор", "бо", "ин", "аст"},
}

# --- латиница ---------------------------------------------------------------
_LAT_LETTERS = {
    "tr": set("ığşİ"),
    "vi": set("ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"),
    "pl": set("ąęłńśźż"),
    "de": set("ß"),
    "es": set("ñ¿¡"),
    "pt": set("ãõ"),
    "fr": set("œèë"),
    "az": set("ə"),
}
_LAT_WORDS = {
    "en": {"the", "and", "for", "you", "with", "this", "that", "is", "are", "will", "market",
           "price", "trading", "to", "of", "in", "on", "now", "your", "we", "it"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "para", "con", "por", "una", "es",
           "mercado", "precio", "como", "pero", "más"},
    "pt": {"o", "a", "os", "as", "de", "que", "e", "para", "com", "por", "uma", "é", "não",
           "mercado", "preço", "você", "mais", "isso"},
    "tr": {"ve", "bir", "bu", "için", "ile", "da", "de", "çok", "daha", "piyasa", "fiyat",
           "olarak", "ama", "gibi", "değil"},
    "id": {"dan", "yang", "untuk", "dengan", "ini", "itu", "di", "ke", "tidak", "pasar",
           "harga", "akan", "kita", "bisa", "ada"},
    "vi": {"và", "của", "là", "có", "không", "cho", "với", "này", "thị", "trường", "giá"},
    "fr": {"le", "la", "les", "et", "pour", "avec", "est", "une", "des", "que", "pas",
           "marché", "prix", "nous", "vous"},
    "de": {"der", "die", "das", "und", "für", "mit", "ist", "nicht", "ein", "eine", "markt",
           "preis", "wir", "sie", "auch"},
    "it": {"il", "lo", "gli", "e", "per", "con", "è", "non", "una", "che", "mercato",
           "prezzo", "anche", "della"},
    "pl": {"i", "w", "na", "z", "że", "jest", "nie", "dla", "się", "rynek", "cena", "oraz"},
    "uz": {"va", "uchun", "pul", "bozor", "bilan", "qanday", "bu", "emas", "narx"},
    "az": {"və", "üçün", "pul", "bazar", "ilə", "necə", "bu", "deyil", "qiymət"},
}

# --- арабская письменность --------------------------------------------------
_FA_LETTERS = set("پچژگی")   # گ پ چ ژ и персидская «ی»
_UR_LETTERS = set("ٹڈڑںے")


def _script_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for name, pattern in _SCRIPTS:
        found = len(pattern.findall(text))
        if found:
            counts[name] = found
    return counts


def _best(scores: Counter[str], fallback: str) -> str:
    if not scores:
        return fallback
    best, value = scores.most_common(1)[0]
    return best if value > 0 else fallback


def _cyrillic_lang(text: str, words: set[str]) -> str:
    letters = set(text)
    scores: Counter[str] = Counter()
    for lang, chars in _CYR_LETTERS.items():
        if letters & chars:
            scores[lang] += 3
    # белорусский: «ў» и «і» без русских «и»/«щ»
    if "ў" in letters and "і" in letters and not ({"и", "щ"} & letters):
        scores["be"] += 3
    if "і" in letters and not (_CYR_LETTERS["kk"] & letters) and "ў" not in letters:
        scores["uk"] += 1
    if {"ы", "э", "ъ", "ё"} & letters:
        scores["ru"] += 2
    for lang, vocab in _CYR_WORDS.items():
        scores[lang] += 2 * len(words & vocab)
    scores["ru"] += 1  # кириллица по умолчанию — русский
    return _best(scores, "ru")


def _latin_lang(text: str, words: set[str]) -> str:
    letters = set(text)
    scores: Counter[str] = Counter()
    for lang, chars in _LAT_LETTERS.items():
        hits = len(letters & chars)
        if hits:
            scores[lang] += 2 + hits
    if "oʻ" in text or "gʻ" in text or "o'" in text or "g'" in text:
        scores["uz"] += 2
    for lang, vocab in _LAT_WORDS.items():
        scores[lang] += 2 * len(words & vocab)
    scores["en"] += 0.5  # латиница без признаков — английский
    return _best(scores, "en")


def _arabic_lang(text: str) -> str:
    letters = set(text)
    if letters & _UR_LETTERS:
        return "ur"
    persian = sum(text.count(c) for c in _FA_LETTERS)
    arabic_only = sum(text.count(c) for c in "ةيىئ")
    if persian > 0 and persian >= arabic_only:
        return "fa"
    return "ar"


def detect_text_language(text: str) -> Optional[str]:
    """Язык одного текста или None, если букв слишком мало."""
    if not text:
        return None
    scripts = _script_counts(text)
    total = sum(scripts.values())
    if total < 12:
        return None
    script = scripts.most_common(1)[0][0]
    low = text.lower()
    if script == "cyrillic":
        return _cyrillic_lang(low, set(_WORD_RE.findall(low)))
    if script == "arabic":
        return _arabic_lang(text)
    if script == "latin":
        return _latin_lang(low, set(_WORD_RE.findall(low)))
    if script in ("kana",):
        return "ja"
    if script == "han":
        return "ja" if scripts.get("kana") else "zh"
    return _SCRIPT_TO_LANG.get(script)


def detect_language(texts: Iterable[str]) -> LanguageResult:
    """Доли языков по постам. Длинные посты весят больше (до 600 символов)."""
    weights: Counter[str] = Counter()
    samples = 0
    for text in texts:
        lang = detect_text_language(text or "")
        if lang is None:
            continue
        samples += 1
        weights[lang] += min(len(text), 600) / 100.0 + 1.0
    if not weights:
        return LanguageResult()

    total = sum(weights.values())
    distribution = {lang: round(value / total, 3) for lang, value in weights.most_common()}
    primary, share = next(iter(distribution.items()))
    if share >= 0.7 and samples >= 5:
        confidence = "high"
    elif share >= 0.5 and samples >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    return LanguageResult(
        primary=primary,
        share=share,
        confidence=confidence,
        distribution=distribution,
        sample_size=samples,
    )


def language_name(code: Optional[str]) -> str:
    return LANGUAGE_NAMES.get(code or "unknown", code or "не определён")
