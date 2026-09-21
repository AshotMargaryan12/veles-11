"""Regex-слой мгновенных алертов (раздел 4.2 ТЗ)."""

from __future__ import annotations

import pytest

from crmai.models import DIRECTION_IN, DIRECTION_OUT, Message
from crmai.redflags import quote_around, scan_message, scan_text


def rules(text):
    return {flag.rule for flag in scan_text(text)}


@pytest.mark.parametrize(
    "text, rule",
    [
        ("Гарантируем доход 20% в месяц", "guaranteed_income"),
        ("гарантированная прибыль для подписчиков", "guaranteed_income"),
        ("Ты точно заработаешь на этом", "sure_earnings"),
        ("Аудитория точно будет зарабатывать", "sure_earnings"),
        ("Это без рисков совсем", "no_risk"),
        ("вообще без всяких рисков", "no_risk"),
        ("Настоящий пассивный доход", "passive_income"),
        ("По рефке отдаём 40%", "ref_rate"),
        ("ставка 50 % для тебя", "ref_rate"),
        ("Оплата пополам: аванс и остаток", "payment_split"),
        ("платим 50/50", "payment_split"),
        ("Фикс будет 3000$", "fix_amount"),
        ("фикс 2500 usd", "fix_amount"),
        ("Сделаем брендированного бота под канал", "branded_bot"),
        ("будет персональный бот для тебя", "branded_bot"),
        ("Верни деньги, иначе пойду к юристам", "pressure"),
        ("подам в суд", "pressure"),
        ("у нас связи в отрасли", "pressure"),
    ],
)
def test_stop_patterns_fire(text, rule):
    assert rule in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "Привет! Смотрел твой разбор сеточных ботов, хочу обсудить формат",
        "Комиссия 20% с прибыли, кап 50$ в месяц",
        "Судя по статистике, конверсия хорошая",
        "Риск-менеджмент в боте настраивается вручную",
        "Пришлю бриф завтра до 18:00",
    ],
)
def test_clean_messages_do_not_fire(text):
    assert rules(text) == set()


def test_proximity_matters():
    # 40% и «ставка» рядом — алерт; далеко друг от друга — нет
    assert "ref_rate" in rules("ставка по рефке 40%")
    far = "40% пользователей доходят до конца ролика. " + "Дальше обсудим формат. " * 5 + "ставка обсуждается"
    assert "ref_rate" not in rules(far)


def test_fix_amount_carries_note():
    flag = next(f for f in scan_text("фикс 3000$") if f.rule == "fix_amount")
    assert "согласована" in flag.note


def test_incoming_messages_are_not_scanned():
    incoming = Message(
        message_id="m", chat_id="c", direction=DIRECTION_IN,
        text="а вы гарантируете доход?", ts="2026-09-21T09:00:00+00:00",
    )
    assert scan_message(incoming) == []

    outgoing = Message(
        message_id="m2", chat_id="c", direction=DIRECTION_OUT,
        text="да, гарантируем доход", ts="2026-09-21T09:00:00+00:00",
    )
    assert [f.rule for f in scan_message(outgoing)] == ["guaranteed_income"]


def test_quote_is_trimmed_around_match():
    text = "а" * 200 + " гарантированный доход " + "б" * 200
    quote = quote_around(text, 201, 224)
    assert quote.startswith("…") and quote.endswith("…")
    assert "гарантированный доход" in quote


def test_empty_text_is_safe():
    assert scan_text("") == []
    assert scan_text("   ") == []
