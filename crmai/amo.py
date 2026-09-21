"""Клиент amoCRM: поиск сделки по контакту, примечания, задачи (раздел 3.2 ТЗ).

Пишем в amo только примечания и задачи — поля сделки не трогаем (раздел 9 ТЗ).

Авторизация: долгоживущая пара access/refresh. На 401 клиент сам обновляет
токены и повторяет запрос один раз. Refresh-токен одноразовый, поэтому новая
пара сразу пишется в файл AMO_TOKEN_FILE — иначе после первого же обновления
интеграция отвалится при рестарте.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional

from .config import Settings, save_amo_tokens
from .retry import retry_call

log = logging.getLogger(__name__)

# Системные статусы amo: сделка закрыта успешно / закрыта с отказом
STATUS_WON = 142
STATUS_LOST = 143
CLOSED_STATUSES = {STATUS_WON, STATUS_LOST}

REQUEST_TIMEOUT = 30


class AmoError(RuntimeError):
    """Ошибка обращения к amoCRM."""


class AmoAuthError(AmoError):
    """Токен протух и обновить его не вышло — нужна ротация вручную."""


class AmoClient:
    """Минимальный клиент REST API v4 под задачи анализатора."""

    def __init__(self, settings: Settings, session: Optional[Any] = None):
        self.settings = settings
        self.access_token = settings.amo_access_token
        self.refresh_token = settings.amo_refresh_token
        if session is not None:
            self.http = session
        else:
            import requests

            self.http = requests.Session()

    # --- низкий уровень ----------------------------------------------------

    def _url(self, path: str) -> str:
        base = self.settings.amo_base_url
        if not base:
            raise AmoError("не задан AMO_DOMAIN")
        if path.startswith("/"):
            return f"{base}{path}"
        return f"{base}/api/v4/{path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        params: Any = None,
        json_body: Any = None,
        _retry_auth: bool = True,
    ) -> Any:
        """Запрос к amo. 204 -> None, 401 -> обновляем токен и повторяем один раз."""
        try:
            response = self.http.request(
                method,
                self._url(path),
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except AmoError:
            raise
        except Exception as exc:  # noqa: BLE001 — таймауты и обрывы тоже наши
            raise AmoError(f"amo: {method} {path} — сетевая ошибка: {exc}") from exc
        status = getattr(response, "status_code", 0)

        if status == 401 and _retry_auth:
            log.info("amo: 401, обновляем токен")
            self.refresh_access_token()
            return self.request(method, path, params, json_body, _retry_auth=False)
        if status == 204:
            return None
        if status in (200, 201):
            try:
                return response.json()
            except ValueError:
                return None
        if status == 401:
            raise AmoAuthError("amo: 401 и после обновления токена")
        raise AmoError(f"amo: {method} {path} вернул HTTP {status}")

    def refresh_access_token(self) -> None:
        """Обновляет пару токенов и сохраняет её на диск."""
        if not (self.settings.amo_client_id and self.settings.amo_client_secret and self.refresh_token):
            raise AmoAuthError(
                "нет AMO_CLIENT_ID/AMO_CLIENT_SECRET/AMO_REFRESH_TOKEN — обновить токен нечем"
            )
        payload = {
            "client_id": self.settings.amo_client_id,
            "client_secret": self.settings.amo_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "redirect_uri": self.settings.amo_redirect_uri,
        }
        try:
            response = self.http.request(
                "POST",
                self._url("/oauth2/access_token"),
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            raise AmoAuthError(f"amo: обновление токена не удалось: {exc}") from exc
        if getattr(response, "status_code", 0) not in (200, 201):
            raise AmoAuthError(
                f"amo: обновление токена вернуло HTTP {getattr(response, 'status_code', '?')}"
            )
        data = response.json() or {}
        access, refresh = data.get("access_token"), data.get("refresh_token")
        if not access or not refresh:
            raise AmoAuthError("amo: в ответе на refresh нет пары токенов")
        self.access_token, self.refresh_token = access, refresh
        self.settings.amo_access_token, self.settings.amo_refresh_token = access, refresh
        save_amo_tokens(self.settings, access, refresh)
        log.info("amo: токен обновлён, новая пара сохранена в %s", self.settings.amo_token_file)

    # --- контакты и сделки -------------------------------------------------

    def search_contacts(self, query: str) -> list[dict[str, Any]]:
        """GET /contacts?query= — ищем по username или телефону из Wazzup."""
        if not query:
            return []
        data = self.request("GET", "contacts", params={"query": query, "with": "leads"})
        if not data:
            return []
        return (data.get("_embedded") or {}).get("contacts") or []

    def contact_leads(self, contact_id: int) -> list[dict[str, Any]]:
        """Сделки контакта. Сначала берём связи из карточки, затем тянем сами сделки."""
        contact = self.request("GET", f"contacts/{contact_id}", params={"with": "leads"})
        lead_ids = [
            lead.get("id")
            for lead in ((contact or {}).get("_embedded") or {}).get("leads") or []
            if lead.get("id")
        ]
        if not lead_ids:
            return []
        return self.leads_by_ids(lead_ids)

    def leads_by_ids(self, lead_ids: Iterable[int]) -> list[dict[str, Any]]:
        params = [("filter[id][]", int(lead_id)) for lead_id in lead_ids]
        if not params:
            return []
        data = self.request("GET", "leads", params=params)
        if not data:
            return []
        return (data.get("_embedded") or {}).get("leads") or []

    def get_lead(self, lead_id: int) -> Optional[dict[str, Any]]:
        try:
            return self.request("GET", f"leads/{lead_id}")
        except AmoError as exc:
            log.warning("amo: сделка %s не получена: %s", lead_id, exc)
            return None

    # --- запись ------------------------------------------------------------

    def add_note(self, lead_id: int, text: str) -> Optional[int]:
        """POST /leads/{id}/notes, тип common. Возвращает id примечания."""
        body = [{"note_type": "common", "params": {"text": text}}]
        data = retry_call(
            lambda: self.request("POST", f"leads/{lead_id}/notes", json_body=body),
            give_up_on=(AmoAuthError,),
            what=f"примечание в сделку {lead_id}",
        )
        notes = ((data or {}).get("_embedded") or {}).get("notes") or []
        return notes[0].get("id") if notes else None

    def create_task(
        self,
        lead_id: int,
        text: str,
        complete_till: int,
        responsible_user_id: int,
        entity_type: str = "leads",
    ) -> Optional[int]:
        """POST /tasks — задача менеджеру, привязанная к сделке."""
        task: dict[str, Any] = {
            "text": text,
            "complete_till": int(complete_till),
            "entity_id": int(lead_id),
            "entity_type": entity_type,
        }
        if responsible_user_id:
            task["responsible_user_id"] = int(responsible_user_id)
        data = retry_call(
            lambda: self.request("POST", "tasks", json_body=[task]),
            give_up_on=(AmoAuthError,),
            what=f"задача по сделке {lead_id}",
        )
        tasks = ((data or {}).get("_embedded") or {}).get("tasks") or []
        return tasks[0].get("id") if tasks else None

    # --- проверка доступа ---------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        """GET /account — быстрая проверка, что токен жив (команда amo-check)."""
        return self.request("GET", "account") or {}


def is_open_lead(lead: dict[str, Any], pipeline_ids: Iterable[int]) -> bool:
    """Открытая сделка в нужной воронке (раздел 3.2 ТЗ)."""
    if lead.get("is_deleted"):
        return False
    if lead.get("closed_at"):
        return False
    if int(lead.get("status_id") or 0) in CLOSED_STATUSES:
        return False
    allowed = {int(pid) for pid in pipeline_ids}
    if allowed and int(lead.get("pipeline_id") or 0) not in allowed:
        return False
    return True


def pick_lead(leads: Iterable[dict[str, Any]], pipeline_ids: Iterable[int]) -> Optional[dict[str, Any]]:
    """Из открытых сделок в наших воронках берём последнюю по updated_at."""
    allowed = list(pipeline_ids)
    candidates = [lead for lead in leads if is_open_lead(lead, allowed)]
    if not candidates:
        return None
    return max(candidates, key=lambda lead: int(lead.get("updated_at") or 0))


def tomorrow_noon(tz: Any = None, now: Optional[datetime] = None) -> int:
    """Unix-время «завтра в 12:00» — срок задачи «Определить следующий шаг»."""
    current = now or (datetime.now(tz) if tz else datetime.now())
    target = datetime.combine(current.date() + timedelta(days=1), time(12, 0))
    if current.tzinfo is not None:
        target = target.replace(tzinfo=current.tzinfo)
    return int(target.timestamp())


def today_in(tz: Any = None) -> date:
    return (datetime.now(tz) if tz else datetime.now()).date()
