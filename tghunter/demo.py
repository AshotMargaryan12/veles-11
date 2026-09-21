"""Демо-режим: тот же конвейер, но вместо Telegram — встроенный корпус каналов.

Нужен, чтобы потрогать инструмент без юзер-сессии и без Telethon: команда
`search --demo` ходит не в сеть, а в этот модуль. Логика поиска, метрик,
скоринга, фильтров, записи в базу и выгрузки — настоящая.

Все каналы вымышленные, префикс `demo_` — чтобы выдачу нельзя было принять
за реальные данные.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import (
    METHOD_KEYWORDS,
    METHOD_MENTIONS,
    METHOD_SIMILAR,
    Candidate,
    ChannelSnapshot,
    Post,
)

log = logging.getLogger(__name__)

AD_POST = "Партнёрский материал #реклама erid: 2Vfnxw{n} — регистрация https://bybit.com/ru?ref=DEMO{n}"
PUMP_POST = "Заходим в памп! x100 за неделю, бесплатные сигналы в закрытом канале"

# username, title, about, подписчики, ср.просмотры, реакции, постов/30д,
# дней с поста, комментарии, теги для поиска, язык постов, продаёт рекламу, стоп-маркеры
_CORPUS: list[tuple] = [
    ("demo_futures_desk", "Фьючерсный стол", "Разбор фьючерсов и альткоинов. По рекламе: @demo_adv_one",
     18400, 2350, 64, 22, 1, True, ["трейдинг", "фьючерсы", "крипта", "криптовалюта", "биржа"], "ru", True, []),
    ("demo_altcoin_notes", "Альткоин-заметки", "Альткоины без воды. Реклама: @demo_adv_two",
     11200, 1180, 41, 14, 2, True, ["альткоины", "крипта", "криптовалюта", "трейдинг"], "ru", True, []),
    ("demo_scalping_lab", "Лаборатория скальпинга", "Скальпинг на споте и фьючерсах. Связь: @demo_adv_three",
     6400, 980, 22, 18, 1, True, ["скальпинг", "трейдинг", "фьючерсы"], "ru", True, []),
    ("demo_btc_watch", "BTC Watch", "Только биткоин, только по делу",
     32000, 3100, 88, 26, 1, True, ["биткоин", "крипта", "криптовалюта"], "ru", False, []),
    ("demo_quiet_analyst", "Тихий аналитик", "Длинные разборы рынка. По рекламе: @demo_adv_four",
     9800, 190, 37, 9, 4, True, ["трейдинг", "аналитика", "крипта"], "ru", True, []),
    ("demo_defi_digest", "DeFi Дайджест", "DeFi, стейкинг, протоколы",
     7300, 720, 19, 11, 3, False, ["defi", "крипта", "стейкинг", "криптовалюта"], "ru", False, []),
    ("demo_exchange_news", "Биржевые новости", "Новости бирж. Реклама: @demo_adv_five",
     46000, 4200, 110, 30, 1, True, ["биржа", "крипта", "новости"], "ru", True, []),

    ("demo_dividend_notes", "Дивидендный блокнот", "Дивиденды и портфель долгосрочного инвестора",
     11200, 1180, 41, 14, 2, True, ["дивиденды", "инвестиции", "акции", "портфель"], "ru", False, []),
    ("demo_stock_ru", "Фондовый рынок РФ", "Акции, облигации, ИИС. Сотрудничество: @demo_adv_six",
     23000, 1900, 52, 17, 2, True, ["акции", "инвестиции", "фондовый рынок", "облигации", "иис"], "ru", True, []),
    ("demo_long_portfolio", "Длинный портфель", "Пассивный доход и индексные фонды",
     4800, 560, 14, 8, 5, True, ["пассивный доход", "портфель", "инвестиции"], "ru", False, []),
    ("demo_bond_desk", "Облигации просто", "Про облигации человеческим языком. Реклама: @demo_adv_seven",
     3900, 410, 11, 9, 3, False, ["облигации", "инвестиции"], "ru", True, []),

    ("demo_money_basics", "Деньги по полочкам", "Финансовая грамотность для начинающих. Связь: @demo_adv_eight",
     8600, 980, 31, 16, 1, True, ["финансовая грамотность", "личные финансы", "накопления"], "ru", True, []),
    ("demo_family_budget", "Семейный бюджет", "Как копить и не срываться",
     5200, 640, 24, 12, 2, True, ["накопления", "личные финансы", "бюджет"], "ru", False, []),
    ("demo_finance_simple", "Финансы просто", "Финансы просто и коротко. По рекламе: @demo_adv_nine",
     13800, 1500, 44, 20, 1, True, ["финансы просто", "финансовая грамотность", "личные финансы"], "ru", True, []),

    ("demo_signal_room", "Сигнальная", "Торговые сигналы и копирование сделок. Реклама: @demo_adv_ten",
     14000, 1700, 39, 24, 1, True, ["сигналы", "копирование сделок", "трейдинг"], "ru", True, []),
    ("demo_pump_squad", "PUMP SQUAD", "Бесплатные сигналы, иксы за день",
     15000, 2600, 120, 40, 1, True, ["сигналы", "памп", "трейдинг"], "ru", True, ["памп", "x100"]),
    ("demo_x100_club", "X100 CLUB", "Разгон депозита, гарантированная прибыль",
     21000, 3400, 150, 35, 1, True, ["сигналы", "трейдинг", "депозит"], "ru", True, ["x100", "гарантированная прибыль"]),
    ("demo_copy_trading", "Копитрейдинг", "Автоследование за сделками",
     6900, 800, 18, 13, 2, False, ["копирование сделок", "автоследование", "сигналы"], "ru", False, []),

    ("demo_kz_finance", "Qarjy KZ", "Қаржы сауаттылығы және ақша туралы. Жарнама: @demo_adv_kz",
     9400, 1100, 27, 15, 2, True, ["қаржы", "инвестиция", "криптовалюта", "kz"], "turkic", True, []),
    ("demo_uz_invest", "Investitsiya UZ", "Moliyaviy savodxonlik va pul ishlash uchun kanal",
     7100, 830, 21, 11, 3, True, ["investitsiya", "kripto", "moliya", "uz"], "turkic", False, []),

    ("demo_london_desk", "London Crypto Desk", "Crypto market and trading notes. Ads: @demo_adv_uk",
     12000, 1500, 30, 12, 2, True, ["crypto", "trading", "market"], "en", True, []),

    ("demo_abandoned_fund", "Заброшенный фонд", "Инвестиции и акции. Контакт: @demo_adv_dead",
     24000, 820, 3, 1, 63, False, ["инвестиции", "акции", "трейдинг"], "ru", False, []),
    ("demo_ghost_trader", "Призрачный трейдер", "Трейдинг и крипта",
     31000, 240, 1, 0, 140, False, ["трейдинг", "крипта"], "ru", False, []),
    ("demo_mega_media", "Мега-медиа", "Всё про деньги и инвестиции. Реклама: @demo_adv_mega",
     310000, 44000, 900, 30, 1, True, ["инвестиции", "крипта", "трейдинг", "финансы"], "ru", True, []),
    ("demo_tiny_club", "Маленький клуб", "Закрытый клуб про инвестиции",
     420, 90, 6, 7, 3, True, ["инвестиции", "клуб"], "ru", False, []),
]

# Кто кому «похож» — имитация channels.GetChannelRecommendations
_SIMILAR: dict[str, list[str]] = {
    "demo_futures_desk": ["demo_altcoin_notes", "demo_scalping_lab", "demo_btc_watch", "demo_exchange_news"],
    "demo_altcoin_notes": ["demo_defi_digest", "demo_futures_desk", "demo_quiet_analyst"],
    "demo_scalping_lab": ["demo_signal_room", "demo_futures_desk", "demo_copy_trading"],
    "demo_btc_watch": ["demo_exchange_news", "demo_defi_digest", "demo_london_desk"],
    "demo_dividend_notes": ["demo_stock_ru", "demo_long_portfolio", "demo_bond_desk"],
    "demo_stock_ru": ["demo_dividend_notes", "demo_bond_desk", "demo_mega_media"],
    "demo_money_basics": ["demo_family_budget", "demo_finance_simple"],
    "demo_finance_simple": ["demo_money_basics", "demo_family_budget", "demo_long_portfolio"],
    "demo_signal_room": ["demo_copy_trading", "demo_pump_squad", "demo_scalping_lab"],
    "demo_kz_finance": ["demo_uz_invest"],
    "demo_exchange_news": ["demo_btc_watch", "demo_futures_desk"],
}

# Кто кого упоминает в постах — имитация графа упоминаний
_MENTIONS: dict[str, list[str]] = {
    "demo_futures_desk": ["demo_quiet_analyst", "demo_defi_digest"],
    "demo_stock_ru": ["demo_bond_desk", "demo_long_portfolio"],
    "demo_money_basics": ["demo_family_budget"],
}

_LANG_TEXT = {
    "ru": "Обзор рынка: что происходит с деньгами инвесторов и как это читать",
    "turkic": "Қаржы нарығы және ақша туралы: bozor va pul uchun qisqa sharh",
    "en": "Market review: what happens to the crypto price and trading volume for you",
}


def _build_snapshot(entry: tuple, now: datetime) -> ChannelSnapshot:
    (username, title, about, subs, views, reactions, posts30, days,
     comments, _tags, lang, monetized, stop_markers) = entry

    posts: list[Post] = []
    # раскладываем posts30 постов по последним 30 дням, начиная с `days` назад
    count = max(posts30, 1)
    step = 30 / count if count else 30
    for i in range(count):
        text = _LANG_TEXT.get(lang, _LANG_TEXT["ru"])
        if monetized and i % 4 == 0:
            text += "\n" + AD_POST.format(n=i)
        if stop_markers and i % 3 == 0:
            text += "\n" + PUMP_POST
        posts.append(
            Post(
                id=i + 1,
                date=now - timedelta(days=days + i * step),
                text=text,
                views=max(int(views * (1 + (i % 5 - 2) * 0.06)), 1),
                reactions=max(int(reactions * (1 + (i % 4 - 1) * 0.1)), 0),
            )
        )

    return ChannelSnapshot(
        channel_id=abs(hash(username)) % 900000 + 1000,
        username=username,
        title=title,
        about=about,
        subscribers=subs,
        has_linked_chat=comments,
        posts=posts,
    )


class DemoGateway:
    """Подмена TelegramGateway: тот же интерфейс, данные из корпуса выше."""

    def __init__(self, now: Optional[datetime] = None):
        self.now = now or datetime.now(timezone.utc)
        self.snapshots: dict[str, ChannelSnapshot] = {}
        self.tags: dict[str, list[str]] = {}
        for entry in _CORPUS:
            snapshot = _build_snapshot(entry, self.now)
            self.snapshots[entry[0]] = snapshot
            self.tags[entry[0]] = [t.lower() for t in entry[9]]
        self.fetch_calls: list[str] = []
        self.search_calls: list[str] = []

    # --- методы поиска ---------------------------------------------------

    def search_keyword(self, keyword: str, limit: int = 30) -> list[Candidate]:
        self.search_calls.append(f"keyword:{keyword}")
        query = keyword.strip().lower()
        if not query:
            return []

        scored: list[tuple[int, str]] = []
        for username, snapshot in self.snapshots.items():
            haystack = " ".join(
                [snapshot.title.lower(), snapshot.about.lower(), username.lower()]
                + self.tags[username]
            )
            weight = 0
            if query in self.tags[username]:
                weight += 3                      # точное совпадение тега
            if query in haystack:
                weight += 2                      # вхождение целиком
            else:
                # частичное совпадение по словам запроса
                words = [w for w in query.split() if len(w) > 3]
                hits = sum(1 for w in words if w in haystack)
                if hits:
                    weight += hits
            if weight:
                scored.append((weight, username))

        scored.sort(key=lambda p: (-p[0], p[1]))
        return [
            Candidate(
                username=username,
                channel_id=self.snapshots[username].channel_id,
                method=METHOD_KEYWORDS,
                source_channel=keyword,
                title=self.snapshots[username].title,
            )
            for _w, username in scored[:limit]
        ]

    def similar_channels(self, username: str, limit: int = 50) -> list[Candidate]:
        clean = username.lstrip("@")
        self.search_calls.append(f"similar:{clean}")
        return [
            Candidate(
                username=name,
                channel_id=self.snapshots[name].channel_id,
                method=METHOD_SIMILAR,
                source_channel=f"@{clean}",
                title=self.snapshots[name].title,
            )
            for name in _SIMILAR.get(clean, [])[:limit]
            if name in self.snapshots
        ]

    def channel_mentions(self, username: str, posts_limit: int = 50) -> list[Candidate]:
        clean = username.lstrip("@")
        self.search_calls.append(f"mentions:{clean}")
        return [
            Candidate(username=name, method=METHOD_MENTIONS, source_channel=f"@{clean}")
            for name in _MENTIONS.get(clean, [])
            if name in self.snapshots
        ]

    def folder_channels(self, url: str) -> list[Candidate]:
        self.search_calls.append(f"folder:{url}")
        return []

    # --- данные канала ----------------------------------------------------

    def fetch_snapshot(
        self,
        username: str,
        posts_limit: int = 30,
        method: str = "manual",
        source_channel: Optional[str] = None,
    ) -> Optional[ChannelSnapshot]:
        clean = username.lstrip("@").lower()
        self.fetch_calls.append(clean)
        snapshot = self.snapshots.get(clean)
        if snapshot is None:
            return None
        snapshot.method = method
        snapshot.source_channel = source_channel
        return snapshot

    def disconnect(self) -> None:
        pass


def corpus_size() -> int:
    return len(_CORPUS)
