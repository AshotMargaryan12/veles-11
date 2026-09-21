"""Клиент Anthropic: один вызов — один JSON-ответ (раздел 2 ТЗ).

Обёртка делает три вещи поверх SDK:
  * следит за дневным лимитом вызовов (MAX_LLM_CALLS_PER_DAY);
  * ретраит сетевые и серверные ошибки (3 попытки, экспоненциальная задержка);
  * разбирает ответ в dict, даже если модель обернула JSON в ```json-блок.

SDK импортируется лениво: тесты и regex-слой работают без установленного
пакета anthropic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Protocol

from .config import Settings
from .db import Database
from .retry import retry_call

log = logging.getLogger(__name__)

# Потолок ответа: статус сделки и список замечаний в него укладываются с запасом
MAX_TOKENS = 4000

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


class BudgetExceeded(RuntimeError):
    """Достигнут MAX_LLM_CALLS_PER_DAY — до конца суток к LLM не ходим."""


class LLMError(RuntimeError):
    """Запрос к LLM не удался или ответ не разобрался в JSON."""


class MessagesClient(Protocol):  # pragma: no cover — только для типизации
    def create(self, **kwargs: Any) -> Any: ...


def extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели.

    Модель просят отвечать строго JSON, но на практике встречается обёртка в
    ```json ... ``` или вводная фраза — вырезаем и то, и другое.
    """
    raw = (text or "").strip()
    if not raw:
        raise LLMError("пустой ответ модели")

    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"ответ модели не похож на JSON: {raw[:200]!r}") from None
        try:
            data = json.loads(raw[start : end + 1])
        except ValueError as exc:
            raise LLMError(f"ответ модели не разобрался в JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMError("ожидали JSON-объект, пришло что-то другое")
    return data


def response_text(response: Any) -> str:
    """Собирает текст из блоков ответа Messages API."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type and block_type != "text":
            continue
        text = getattr(block, "text", None) or (
            block.get("text") if isinstance(block, dict) else None
        )
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


class LLMClient:
    """Вызовы Anthropic Messages API с бюджетом и ретраями."""

    def __init__(
        self,
        settings: Settings,
        db: Optional[Database] = None,
        client: Optional[Any] = None,
    ):
        self.settings = settings
        self.db = db
        self._client = client

    @property
    def client(self) -> Any:
        """SDK создаётся при первом обращении — ключ берётся из ANTHROPIC_API_KEY."""
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover — зависимость не стоит
                raise LLMError(
                    "не установлен пакет anthropic (pip install -r requirements-crmai.txt)"
                ) from exc
            if not self.settings.anthropic_api_key:
                raise LLMError("не задан ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    # --- бюджет -----------------------------------------------------------

    def calls_left(self, day: str) -> int:
        if self.db is None:
            return self.settings.max_llm_calls_per_day
        return max(0, self.settings.max_llm_calls_per_day - self.db.llm_calls_today(day))

    def check_budget(self, day: str) -> None:
        if self.calls_left(day) <= 0:
            raise BudgetExceeded(
                f"достигнут лимит {self.settings.max_llm_calls_per_day} вызовов LLM за {day}"
            )

    # --- вызовы -----------------------------------------------------------

    def complete(self, system: str, user: str, day: str, max_tokens: int = MAX_TOKENS) -> str:
        """Один запрос к модели. Считает вызов в бюджет даже при ошибке ответа."""
        self.check_budget(day)

        def _call() -> Any:
            return self.client.messages.create(
                model=self.settings.llm_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        if self.db is not None:
            self.db.bump_llm_calls(day)

        try:
            response = retry_call(_call, what="запрос к LLM")
        except Exception as exc:  # noqa: BLE001 — превращаем в доменную ошибку
            raise LLMError(str(exc)) from exc

        text = response_text(response)
        # В лог INFO — только факт вызова, тексты переписки туда не попадают
        log.info("LLM: ответ получен, %d символов", len(text))
        log.debug("LLM ответ: %s", text)
        return text

    def complete_json(
        self, system: str, user: str, day: str, max_tokens: int = MAX_TOKENS
    ) -> dict[str, Any]:
        """Запрос, ответ которого обязан быть JSON-объектом."""
        return extract_json(self.complete(system, user, day, max_tokens=max_tokens))


class StubLLM:
    """Заглушка для демо-режима и тестов: отдаёт заранее заданные ответы."""

    def __init__(self, responses: Optional[list[str]] = None, default: str = "{}"):
        self.responses = list(responses or [])
        self.default = default
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, day: str, max_tokens: int = MAX_TOKENS) -> str:
        self.calls.append((system, user))
        if self.responses:
            return self.responses.pop(0)
        return self.default

    def complete_json(
        self, system: str, user: str, day: str, max_tokens: int = MAX_TOKENS
    ) -> dict[str, Any]:
        return extract_json(self.complete(system, user, day, max_tokens=max_tokens))

    def calls_left(self, day: str) -> int:
        return 10**6

    def check_budget(self, day: str) -> None:
        return None
