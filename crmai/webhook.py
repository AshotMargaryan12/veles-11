"""FastAPI-приложение: приём вебхуков Wazzup (раздел 3.1 ТЗ).

Главное требование — отвечать 200 в пределах двух секунд, иначе Wazzup
начинает ретраить. Поэтому обработка (запись в базу, regex-слой, алерты)
уходит в фон, а эндпоинт отвечает сразу после чтения тела.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Optional

# FastAPI импортируется на уровне модуля: из-за `from __future__ import annotations`
# аннотации вычисляются в пространстве имён модуля, и локальный импорт ломает
# разбор параметров эндпоинта. Сам модуль подключается лениво — только в `serve`.
from fastapi import BackgroundTasks, FastAPI, Header, Query, Request, Response

from .config import Settings, load_amo_tokens, load_settings
from .service import Services, build_services

log = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/wazzup"


def _check_secret(settings: Settings, header: Optional[str], query: Optional[str]) -> bool:
    """Необязательная защита эндпоинта общим секретом."""
    expected = settings.wazzup_webhook_secret
    if not expected:
        return True
    return any(
        candidate is not None and hmac.compare_digest(expected, candidate)
        for candidate in (header, query)
    )


def create_app(services: Optional[Services] = None, settings: Optional[Settings] = None) -> Any:
    """Собирает приложение вокруг уже созданных сервисов."""
    settings = settings or (services.settings if services else load_amo_tokens(load_settings()))
    services = services or build_services(settings)

    app = FastAPI(
        title="Veles CRM analyzer",
        description="Анализ диалогов Wazzup -> LLM -> amoCRM. Сообщения не отправляет.",
        version="0.1.0",
    )
    app.state.services = services

    @app.get("/health")
    def health() -> dict[str, Any]:
        stats = services.db.stats()
        return {
            "status": "ok",
            "amo": bool(services.amo),
            "llm": bool(services.llm),
            "telegram": bool(services.notifier),
            "messages": stats["messages"],
            "chats": stats["chats"],
        }

    @app.post(WEBHOOK_PATH)
    async def wazzup_webhook(
        request: Request,
        background: BackgroundTasks,
        response: Response,
        x_webhook_secret: Optional[str] = Header(default=None),
        secret: Optional[str] = Query(default=None),
    ) -> dict[str, Any]:
        if not _check_secret(settings, x_webhook_secret, secret):
            log.warning("Вебхук с неверным секретом отклонён")
            response.status_code = 403
            return {"ok": False, "error": "forbidden"}

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — тело может быть пустым или битым
            log.warning("Вебхук с нечитаемым телом — отвечаем 200, чтобы не копить ретраи")
            return {"ok": True, "ignored": True}

        # Обработка в фоне: ответ уходит сразу, Wazzup не ретраит
        background.add_task(_process, services, payload)
        return {"ok": True}

    return app


def _process(services: Services, payload: Any) -> None:
    """Фоновая обработка события. Любая ошибка гасится — вебхук уже ответил 200."""
    try:
        if services.intake is None:
            log.error("Intake не собран — событие потеряно")
            return
        services.intake.handle_payload(payload)
    except Exception as exc:  # noqa: BLE001
        log.exception("Обработка события Wazzup упала: %s", exc)


def run(settings: Optional[Settings] = None, services: Optional[Services] = None,
        host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Запуск uvicorn (команда `serve`)."""
    import uvicorn

    settings = settings or (services.settings if services else load_amo_tokens(load_settings()))
    app = create_app(services=services, settings=settings)
    uvicorn.run(app, host=host or settings.webhook_host, port=port or settings.webhook_port)
