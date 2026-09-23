"""Реестр источников: какой шлюз поднять и что для него нужно."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..config import Settings
from ..ratelimit import RateLimiter

log = logging.getLogger(__name__)

SOURCE_TELEGRAM = "telegram"
SOURCE_TGSTAT = "tgstat"
SOURCE_DEMO = "demo"

SOURCES = (SOURCE_TELEGRAM, SOURCE_TGSTAT, SOURCE_DEMO)


@dataclass
class SourceInfo:
    """Описание источника для UI настроек и команды setup."""

    key: str
    title: str
    needs: str
    ready: bool
    hint: str
    methods: str


def describe_sources(settings: Settings) -> list[SourceInfo]:
    """Что настроено, а что нет — для страницы настроек и `setup`."""
    has_session = _session_exists(settings)
    telegram_keys = bool(settings.api_id and settings.api_hash)

    return [
        SourceInfo(
            key=SOURCE_TELEGRAM,
            title="Telegram (MTProto)",
            needs="TG_API_ID + TG_API_HASH и один раз вход по телефону",
            ready=telegram_keys and has_session,
            hint=(
                "Полный набор: глобальный поиск, нативные рекомендации, граф упоминаний, "
                "реакции и комментарии."
                if telegram_keys and has_session
                else (
                    "Ключи есть, осталось войти: в терминале `python -m tghunter login`"
                    if telegram_keys
                    else "Получите api_id и api_hash на my.telegram.org, затем войдите командой login"
                )
            ),
            methods="keywords, similar, mentions, folders",
        ),
        SourceInfo(
            key=SOURCE_TGSTAT,
            title="TGStat API",
            needs="TGSTAT_TOKEN",
            ready=bool(settings.tgstat_token),
            hint=(
                "Работает сразу по ключу, без входа по телефону. Не отдаёт реакции, "
                "комментарии и нативные рекомендации."
                if settings.tgstat_token
                else "Ключ из личного кабинета api.tgstat.ru — поиск заработает сразу, без сессии."
            ),
            methods="keywords, mentions",
        ),
        SourceInfo(
            key=SOURCE_DEMO,
            title="Демо-корпус",
            needs="ничего",
            ready=True,
            hint="25 вымышленных каналов. Сеть не используется — чтобы освоиться до боевого прогона.",
            methods="keywords, similar, mentions",
        ),
    ]


def source_status(settings: Settings, key: str) -> Optional[SourceInfo]:
    for info in describe_sources(settings):
        if info.key == key:
            return info
    return None


def _session_exists(settings: Settings) -> bool:
    from pathlib import Path

    return Path(f"{settings.session_path}.session").exists()


def build_source(
    settings: Settings, limiter: RateLimiter, key: str = SOURCE_TELEGRAM
) -> Any:
    """Поднимает шлюз выбранного источника."""
    if key == SOURCE_DEMO:
        from ..demo import DemoGateway, corpus_size

        log.info("Источник demo: встроенный корпус из %d каналов, сеть не используется",
                 corpus_size())
        return DemoGateway()

    if key == SOURCE_TGSTAT:
        from .tgstat import TGStatGateway

        log.info("Источник tgstat: работаем по API-ключу, юзер-сессия не нужна")
        return TGStatGateway(settings.tgstat_token, limiter=limiter)

    if key == SOURCE_TELEGRAM:
        from ..tg import TelegramGateway

        gateway = TelegramGateway(settings, limiter)
        gateway.connect()
        return gateway

    raise ValueError(f"Неизвестный источник: {key}. Доступны: {', '.join(SOURCES)}")
