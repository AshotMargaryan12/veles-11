"""Сборка компонентов системы из настроек.

Одна точка, где решается, что создаём, а что нет: без ANTHROPIC_API_KEY нет
LLM, без AMO_DOMAIN нет клиента amo, без токена бота нет уведомлений. Это
позволяет запускать приём вебхуков и regex-алерты даже до того, как заведены
все интеграции.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .amo import AmoClient
from .config import Settings, load_amo_tokens, load_settings
from .db import Database
from .intake import Intake
from .llm import LLMClient
from .prompts import Prompts, PromptError
from .telegram import TelegramNotifier

log = logging.getLogger(__name__)


@dataclass
class Services:
    """Всё, что нужно батчам и вебхуку."""

    settings: Settings
    db: Database
    notifier: Optional[TelegramNotifier] = None
    amo: Optional[AmoClient] = None
    llm: Optional[Any] = None
    prompts: Optional[Prompts] = None
    intake: Optional[Intake] = None

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Services":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def build_services(
    settings: Optional[Settings] = None,
    db: Optional[Database] = None,
    llm: Optional[Any] = None,
    amo: Optional[AmoClient] = None,
    notifier: Optional[TelegramNotifier] = None,
    with_prompts: bool = True,
    use_amo: bool = True,
    use_llm: bool = True,
    use_telegram: bool = True,
) -> Services:
    """Создаёт компоненты, пропуская ненастроенные.

    Готовые объекты можно передать аргументами (так делают тесты и демо), а
    флаги use_* выключают компонент даже при заполненном .env.
    """
    settings = settings or load_amo_tokens(load_settings())
    database = db or Database(settings.db_path)

    if notifier is None and use_telegram and settings.telegram_enabled:
        notifier = TelegramNotifier(settings)
    elif notifier is None:
        log.info("Telegram не настроен — алерты и дайджест только в лог")

    if amo is None and use_amo and settings.amo_enabled:
        amo = AmoClient(settings)
    elif amo is None:
        log.info("amoCRM не настроен — примечания и задачи писать некуда")

    if llm is None and use_llm and settings.llm_enabled:
        llm = LLMClient(settings, database)
    elif llm is None:
        log.info("ANTHROPIC_API_KEY не задан — батчи анализа работать не будут")

    prompts = None
    if with_prompts:
        try:
            prompts = Prompts.load(settings.prompts_dir)
        except PromptError as exc:
            log.warning("Промпты не загружены: %s", exc)

    return Services(
        settings=settings,
        db=database,
        notifier=notifier,
        amo=amo,
        llm=llm,
        prompts=prompts,
        intake=Intake(settings, database, notifier=notifier, amo=amo),
    )
