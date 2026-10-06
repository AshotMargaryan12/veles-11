"""Язык контента и эвристика гео (раздел 4.4 ТЗ)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qualifier.config import load_geo_profiles
from qualifier.geo import estimate_geo, find_markers
from qualifier.lang import detect_language, detect_text_language

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def profiles():
    return load_geo_profiles(REPO / "config" / "qualifier")


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Сегодня разбираем рынок: биткоин и альткоины, что делать с позицией", "ru"),
        ("Сьогодні розбираємо ринок: біткоїн та альткоїни, що робити з позицією", "uk"),
        ("Бүгін нарық туралы: биткоин және альткоиндер, қаржы үшін не істеу керек", "kk"),
        ("Сёння разбіраем рынак: біткоін і альткоіны, што рабіць з пазіцыяй ўжо", "be"),
        ("إيردروب جديد! اعمل claim للنقاط قبل نهاية الأسبوع والتحويل على فودافون كاش", "ar"),
        ("امروز بازار را بررسی می‌کنیم: بیت کوین و آلت کوین‌ها چگونه پیش می‌روند", "fa"),
        ("Today we review the market: bitcoin and altcoins, what to do with the position", "en"),
        ("Bugün piyasayı inceliyoruz: bitcoin ve altcoinler için çok önemli bir hafta", "tr"),
        ("Hoy analizamos el mercado: bitcoin y las altcoins, qué hacer con la posición", "es"),
        ("Hoje analisamos o mercado: bitcoin e as altcoins, o que fazer com a posição você", "pt"),
        ("Hari ini kita bahas pasar: bitcoin dan altcoin yang akan naik untuk kita", "id"),
        ("आज हम बाजार का विश्लेषण करते हैं: बिटकॉइन और ऑल्टकॉइन", "hi"),
    ],
)
def test_text_language(text, lang):
    assert detect_text_language(text) == lang


def test_short_text_is_skipped():
    assert detect_text_language("🚀🚀 ok") is None


def test_language_distribution_and_confidence():
    texts = ["Разбор рынка: биткоин растёт, что делать трейдерам сейчас"] * 8
    texts += ["Market update: bitcoin is up and altcoins follow the trend"] * 2
    result = detect_language(texts)
    assert result.primary == "ru"
    assert result.confidence == "high"
    assert 0.7 < result.share < 1
    assert "en" in result.distribution


def test_marker_matching_rules():
    texts = ["Индия снова в новостях", "TL;DR: рынок вырос", "Купил за 500 TL на Papara"]
    hits = find_markers(texts, ["дия", "re:\\d\\s?tl\\b", "papara"])
    # «дия» не находится внутри «Индия», TL;DR — не валюта
    assert "дия" not in hits
    assert hits == {"re:\\d\\s?tl\\b": 1, "papara": 1}


def test_short_cyrillic_marker_matches_word_start():
    hits = find_markers(["Вывожу через Сбербанк и СБП"], ["сбер", "сбп"])
    assert hits == {"сбер": 1, "сбп": 1}


def _lang(texts):
    return detect_language(texts)


def test_egypt_medium_from_language_and_markers(profiles):
    texts = [
        "مكافأة 500 جنيه لأول 100 مشترك، التحويل على فودافون كاش",
        "إيردروب جديد، اعمل claim قبل نهاية الأسبوع يا شباب",
        "اسحب أرباحك على فودافون كاش بسهولة",
        "تحديث السوق: البيتكوين فوق 60 ألف والألتكوينز بتتحرك",
    ]
    geo = estimate_geo(texts, _lang(texts), profiles)
    assert geo.country == "EG"
    assert geo.confidence == "medium"
    assert geo.needs_confirmation
    assert any(s.kind == "markers" for s in geo.signals)


def test_profile_country_raises_confidence(profiles):
    texts = [
        "مكافأة 500 جنيه لأول 100 مشترك، التحويل على فودافون كاش",
        "اسحب أرباحك على انستاباي بسهولة من القاهرة",
        "إيردروب جديد، اعمل claim قبل نهاية الأسبوع يا شباب",
    ]
    geo = estimate_geo(texts, _lang(texts), profiles, profile_countries=["EG"])
    assert geo.country == "EG"
    assert geo.confidence == "high"
    assert not geo.needs_confirmation


def test_russian_without_markers_is_low(profiles):
    texts = ["Разбор рынка: биткоин растёт, что делать трейдерам сейчас"] * 6
    geo = estimate_geo(texts, _lang(texts), profiles)
    assert geo.country == "RU"
    assert geo.confidence == "low"
    assert {code for code, _ in geo.alternatives} >= {"UA", "KZ"}


@pytest.mark.parametrize(
    "marker_text, country",
    [
        ("Выводим в гривны через Монобанк, курс грн сегодня", "UA"),
        ("Пополнение через Kaspi, курс тенге, встреча в Алматы", "KZ"),
        ("Оплата через ЕРИП, курс BYN, Беларусбанк снова поднял ставки", "BY"),
        ("Вывожу в рубли на Сбер через СБП, НДФЛ плачу сам", "RU"),
    ],
)
def test_russian_language_cis_markers(profiles, marker_text, country):
    texts = ["Разбор рынка: биткоин растёт, что делать трейдерам сейчас"] * 4
    texts += [marker_text, marker_text]
    geo = estimate_geo(texts, _lang(texts), profiles)
    assert geo.country == country
    assert geo.confidence in ("medium", "high")


def test_manual_geo_overrides(profiles):
    geo = estimate_geo(["anything"], _lang(["anything"]), profiles, manual="eg")
    assert geo.country == "EG"
    assert geo.confidence == "manual"
    assert geo.source == "manual"
    assert not geo.needs_confirmation
    assert geo.country_name == "Египет"
    assert geo.country_name_en == "Egypt"


def test_llm_hint_is_one_signal(profiles):
    texts = ["Market update: bitcoin is up and altcoins follow the trend"] * 5
    geo = estimate_geo(texts, _lang(texts), profiles, llm_country="NG", llm_evidence=["mentions naira"])
    assert geo.country == "NG"
    assert geo.confidence == "low"  # одной подсказки LLM мало для уверенности
    assert any(s.kind == "llm" for s in geo.signals)


def test_no_signals_returns_unknown(profiles):
    geo = estimate_geo([], detect_language([]), profiles)
    assert geo.country is None
    assert geo.confidence == "low"


def test_latin_markers_match_whole_words(profiles):
    texts = ["New payments infrastructure for crypto users across the market"] * 3
    geo = estimate_geo(texts, _lang(texts), profiles)
    assert geo.country is None               # «payme» не находится в «payments»


def test_english_without_signals_is_not_determined(profiles):
    texts = ["Market update: bitcoin is up and altcoins follow the trend"] * 6
    geo = estimate_geo(texts, _lang(texts), profiles)
    assert geo.country is None and geo.confidence == "low"
    assert geo.needs_confirmation
