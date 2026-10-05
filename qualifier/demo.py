"""Демо-режим: вымышленные площадки и заготовленные ответы LLM.

`qualify --demo t.me/demo_airdrop_eg youtube.com/@demo_airdrop_eg` показывает
полную карточку без Telegram-сессии, ключей и сети. Все каналы и цифры выдуманы.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Optional

from .models import (
    PLATFORM_TELEGRAM,
    PLATFORM_X,
    PLATFORM_YOUTUBE,
    STATUS_NOT_FOUND,
    STATUS_UNAVAILABLE,
    ContentItem,
    PlatformData,
    PlatformRef,
    utcnow,
)

DEMO_LINKS = [
    "t.me/demo_airdrop_eg youtube.com/@demo_airdrop_eg",
    "t.me/demo_futures_ru x.com/demo_futures_ru",
    "t.me/demo_pump_signals",
]

_AR_POSTS = [
    "🚀 إيردروب جديد! اعمل claim للنقاط قبل نهاية الأسبوع، الرابط في التعليقات",
    "شرح خطوة بخطوة: إزاي تشارك في التستنت وتجمع البوينتس مجانًا",
    "تحديث السوق: البيتكوين فوق 60 ألف، الألتكوينز بتتحرك. هنعمل صفقة سبوت صغيرة",
    "مكافأة 500 جنيه لأول 100 مشترك 🎁 التحويل على فودافون كاش",
    "#إعلان منصة جديدة للتداول، سجل من الرابط https://partner-exchange.example/register?ref=MASR",
    "إيردروب Layer3: المهام سهلة والـ claim خلال يومين",
    "اسحب أرباحك على انستاباي بسهولة، تجربتي الشخصية",
    "قائمة أفضل 5 مشاريع ايردروب الشهر ده 🔥",
    "صفقة فيوتشر على SOL بهدف 5%، إدارة رأس المال أهم حاجة",
    "سؤال اليوم: مين جرب الريترودروب على zkSync؟",
]

_RU_FUTURES_POSTS = [
    "Разбор сделки по BTC: шорт от 64 200, стоп 64 900, тейк 62 800. Риск 1% депозита.",
    "Риск-менеджмент: почему плечо x20 убивает депозит. Считаем позицию на примере.",
    "Фьючерсы на ETH: открытый интерес растёт, фандинг отрицательный — ждём шортсквиз.",
    "#реклама erid: 2VtzqwXyZ1 Курс по трейдингу от партнёра, промокод на скидку внутри",
    "Итоги недели: 7 сделок, 5 в плюс. Журнал сделок в комментариях.",
    "Как я вывожу прибыль в рубли: Сбер и Тинькофф, налоги и НДФЛ для трейдера.",
    "Спот или фьючерсы новичку? Мой ответ — сначала спот, потом малое плечо.",
    "Альткоины: смотрю SOL и TON, уровни на графике.",
    "Стрим в четверг: торгуем вместе с чатом, разбор ваших сделок.",
    "Психология трейдинга: как не отыгрываться после стопа.",
]

_RU_PUMP_POSTS = [
    "🔥 СИГНАЛ: монета XYZ, вход сейчас, 100% прибыль гарантирована! Иксы за неделю!",
    "Наша памп-группа снова принесла x5. Следующий памп в 20:00 МСК, не пропусти",
    "VIP-доступ: удвоим депозит за месяц без риска. Пиши админу",
    "Пампим новый токен вместе, заходим все одновременно!",
    "Результаты VIP: +340% за неделю 🚀🚀🚀",
]

_YT_TITLES = [
    "إزاي تجمع إيردروب 1000$ مجانًا | شرح كامل",
    "أفضل منصات التداول للمصريين 2026",
    "شرح Layer3 خطوة بخطوة",
    "ايردروب جديد قبل ما يخلص!",
    "تحليل البيتكوين الأسبوع ده",
]


def _posts(texts: list[str], count: int, now: datetime, step_hours: float, views: int,
           spread: float, reactions: int, seed: int, spikes: Optional[dict[int, int]] = None,
           start_hours: float = 3.0, comments: Optional[int] = None) -> list[ContentItem]:
    rnd = random.Random(seed)
    items = []
    for i in range(count):
        v = int(views * rnd.uniform(1 - spread, 1 + spread))
        if spikes and i in spikes:
            v = spikes[i]
        items.append(ContentItem(
            id=str(1000 - i),
            date=now - timedelta(hours=start_hours + i * step_hours),
            text=texts[i % len(texts)],
            views=v,
            reactions=max(0, int(reactions * rnd.uniform(0.5, 1.5))),
            comments=None if comments is None else max(0, int(comments * rnd.uniform(0.5, 1.5))),
        ))
    return items


def _fixtures(now: datetime) -> dict[tuple[str, str], PlatformData]:
    eg_tg = PlatformData(
        platform=PLATFORM_TELEGRAM, handle="demo_airdrop_eg", url="https://t.me/demo_airdrop_eg",
        title="Crypto Masr | كريبتو مصر", username="demo_airdrop_eg",
        description="قناة الإيردروب الأولى في مصر 🇪🇬 اربح من الكريبتو يوميًا بدون خبرة\n"
                    "للإعلانات: @masr_ads",
        followers=13556, has_community_chat=True, community_members=1230,
        community_title="Crypto Masr Chat",
        items=_posts(_AR_POSTS, 50, now, step_hours=30, views=1820, spread=0.25, reactions=41,
                     seed=1, comments=12),
        items_limit=50, source="demo",
    )
    eg_yt = PlatformData(
        platform=PLATFORM_YOUTUBE, handle="demo_airdrop_eg", url="https://www.youtube.com/@demo_airdrop_eg",
        title="Crypto Masr", username="@demo_airdrop_eg",
        description="قناة عن الإيردروب والكريبتو في مصر", followers=4200, country="EG",
        items=_posts(_YT_TITLES, 10, now, step_hours=96, views=900, spread=0.3, reactions=60,
                     seed=2, start_hours=80, comments=15),
        items_limit=10, source="demo",
    )
    for item in eg_yt.items:
        item.duration_sec = 600

    ru_tg = PlatformData(
        platform=PLATFORM_TELEGRAM, handle="demo_futures_ru", url="https://t.me/demo_futures_ru",
        title="Фьючерсы без иллюзий", username="demo_futures_ru",
        description="Торгую фьючерсами с 2019 года. Сделки, риск-менеджмент, разборы.\n"
                    "По рекламе и сотрудничеству: @futures_adv",
        followers=8400, has_community_chat=True, community_members=2100,
        community_title="Чат трейдеров",
        items=_posts(_RU_FUTURES_POSTS, 50, now, step_hours=20, views=2300, spread=0.2,
                     reactions=55, seed=3, comments=25),
        items_limit=50, source="demo",
    )

    pump_tg = PlatformData(
        platform=PLATFORM_TELEGRAM, handle="demo_pump_signals", url="https://t.me/demo_pump_signals",
        title="PUMP SIGNALS 💎 VIP", username="demo_pump_signals",
        description="Лучшие сигналы! Гарантированный доход каждый день. VIP — пиши @pump_admin",
        followers=42000, has_community_chat=False,
        items=_posts(_RU_PUMP_POSTS, 40, now, step_hours=14, views=600, spread=0.3, reactions=1,
                     seed=4, spikes={4: 15000, 9: 12000, 15: 14000}),
        items_limit=50, source="demo",
    )
    for item in pump_tg.items:
        item.reactions = None  # реакции отключены
    return {
        (PLATFORM_TELEGRAM, "demo_airdrop_eg"): eg_tg,
        (PLATFORM_YOUTUBE, "demo_airdrop_eg"): eg_yt,
        (PLATFORM_TELEGRAM, "demo_futures_ru"): ru_tg,
        (PLATFORM_TELEGRAM, "demo_pump_signals"): pump_tg,
    }


class DemoCollector:
    """Отдаёт вымышленные площадки; X в демо — «данные недоступны», как без токена."""

    def __init__(self, platform: str, now: Optional[datetime] = None):
        self.platform = platform
        self.now = now or utcnow()
        self._data = _fixtures(self.now)

    def collect(self, ref: PlatformRef) -> PlatformData:
        if ref.platform == PLATFORM_X:
            return PlatformData(
                platform=PLATFORM_X, handle=ref.handle, url=ref.url, status=STATUS_UNAVAILABLE,
                status_reason="нет X_BEARER_TOKEN (демо)", source="demo",
            )
        data = self._data.get((ref.platform, ref.handle.lower()))
        if data is None:
            return PlatformData(
                platform=ref.platform, handle=ref.handle, url=ref.url, status=STATUS_NOT_FOUND,
                status_reason="в демо-корпусе нет такой площадки", source="demo",
            )
        return PlatformData.from_dict(data.to_dict())

    def close(self) -> None:
        pass


def demo_collectors(now: Optional[datetime] = None) -> dict[str, DemoCollector]:
    return {p: DemoCollector(p, now) for p in (PLATFORM_TELEGRAM, PLATFORM_YOUTUBE, PLATFORM_X)}


# --------------------------------------------------------------------------
# Заготовленные ответы «LLM»
# --------------------------------------------------------------------------

def _a(value: str, evidence: list[str], **extra: Any) -> dict[str, Any]:
    return {"value": value, "evidence": evidence, **extra}


_ASSESSMENTS: dict[str, dict[str, Any]] = {
    "demo_airdrop_eg": {
        "audience_type": _a("airdrop_hunters", [
            "«إيردروب جديد! اعمل claim للنقاط قبل نهاية الأسبوع» [TG#1]",
            "Большинство постов — про раздачи, тестнеты и клейм поинтов",
        ], secondary="traders"),
        "conversion_forecast": _a("low", ["Торговые посты единичны: «صفقة فيوتشر على SOL» [TG#9]"],
                                  reason="преобладает контент про раздачи и клейм-поинты"),
        "topic_focus": {"values": ["altcoins", "other"], "status": "ok",
                        "evidence": ["айрдропы и альткоины, редкие сделки"]},
        "red_flags": {"status": "found", "items": [
            {"type": "get_rich_quick", "quote": "اربح من الكريبتو يوميًا بدون خبرة",
             "comment": "в описании канала обещание ежедневного заработка без опыта"},
            {"type": "competitor_referrals",
             "quote": "#إعلان منصة جديدة للتداول، سجل من الرابط",
             "comment": "реферальная ссылка на другую биржу"},
        ]},
        "tone": _a("hype", ["эмодзи-ракеты и «🔥» в заголовках, призывы успеть"],
                   brand_fit="acceptable"),
        "airdrop_share": _a("dominant", ["около 6 из 10 постов — айрдропы и клейм"]),
        "geo_hint": {"country": "EG", "evidence": [
            "«مكافأة 500 جنيه لأول 100 مشترك» [TG#4] — египетский фунт",
            "«التحويل على فودافون كاش» [TG#4] — египетская платёжка",
        ]},
    },
    "demo_futures_ru": {
        "audience_type": _a("traders", [
            "«Разбор сделки по BTC: шорт от 64 200, стоп 64 900» [TG#1]",
            "Регулярные разборы сделок и риск-менеджмент",
        ], secondary="beginners"),
        "conversion_forecast": _a("high", ["«Итоги недели: 7 сделок, 5 в плюс» [TG#5]"],
                                  reason="аудитория уже торгует фьючерсами, контент про конкретные сделки"),
        "topic_focus": {"values": ["futures", "spot", "education"], "status": "ok",
                        "evidence": ["фьючерсы, плечо, разборы сделок"]},
        "red_flags": {"status": "none_found", "items": []},
        "tone": _a("professional", ["«Риск 1% депозита» [TG#1] — акцент на риск-менеджменте"],
                   brand_fit="good"),
        "airdrop_share": _a("none", ["айрдроп-контента нет"]),
        "geo_hint": {"country": "RU", "evidence": [
            "«Как я вывожу прибыль в рубли: Сбер и Тинькофф» [TG#6]",
        ]},
    },
    "demo_pump_signals": {
        "audience_type": _a("off_target", [
            "«Наша памп-группа снова принесла x5» [TG#2]",
        ], secondary="none"),
        "conversion_forecast": _a("low", ["аудитория пришла за пампами, а не за торговлей"],
                                  reason="памп-группа, аудитория ждёт быстрых иксов"),
        "topic_focus": {"values": ["signals"], "status": "ok", "evidence": ["только сигналы и пампы"]},
        "red_flags": {"status": "found", "items": [
            {"type": "guaranteed_returns", "quote": "100% прибыль гарантирована",
             "comment": "прямое обещание гарантированной доходности"},
            {"type": "pump", "quote": "Следующий памп в 20:00 МСК",
             "comment": "организация пампов"},
        ]},
        "tone": _a("aggressive", ["капслок и обещания иксов"], brand_fit="poor"),
        "airdrop_share": _a("none", ["айрдропов нет"]),
        "geo_hint": {"country": "insufficient_data", "evidence": ["кроме «МСК» местных реалий нет"]},
    },
}

_EMPTY = {
    "audience_type": _a("insufficient_data", ["нет контента"], secondary="none"),
    "conversion_forecast": _a("insufficient_data", ["нет контента"], reason=""),
    "topic_focus": {"values": [], "status": "insufficient_data", "evidence": ["нет контента"]},
    "red_flags": {"status": "insufficient_data", "items": []},
    "tone": _a("insufficient_data", ["нет контента"], brand_fit="insufficient_data"),
    "airdrop_share": _a("insufficient_data", ["нет контента"]),
    "geo_hint": {"country": "insufficient_data", "evidence": ["нет контента"]},
}


class _DemoMessages:
    def create(self, **kwargs: Any) -> Any:
        schema = kwargs["output_config"]["format"]["schema"]
        user = kwargs["messages"][0]["content"]
        if "audience_type" in schema["properties"]:
            payload = next((v for k, v in _ASSESSMENTS.items() if k in user), _EMPTY)
        else:
            payload = {name: _demo_slot(name, user) for name in schema["properties"]}
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))],
        )


def _demo_slot(name: str, facts: str) -> str:
    if name == "AUDIENCE_SUMMARY":
        return ("[demo] Audience summary written by the LLM from the public facts: who follows "
                "the partner and how likely they are to trade.")
    if name == "JUSTIFICATION":
        return "- [demo] Strong reach relative to audience size\n- [demo] Active posting and a live chat"
    if name == "RISKS":
        _, _, tail = facts.partition("Risk flags:")
        risky = [line[2:] for line in tail.splitlines() if line.startswith("- ")]
        return "\n".join(f"- [demo] {line}" for line in risky) or "- [demo] No automatic flags; geo to be confirmed"
    return f"[demo] {name}"


class DemoAnthropic:
    """Подмена anthropic.Anthropic для демо: детерминированные ответы, без сети."""

    def __init__(self) -> None:
        self.messages = _DemoMessages()
