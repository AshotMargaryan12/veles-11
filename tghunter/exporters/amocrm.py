"""Выгрузка в amoCRM (раздел 8.3 ТЗ, опционально, флаг --amo).

Создаёт сделки в воронке хантинга со статусом «Новый». Дедуп на стороне amo
по ссылке канала: перед созданием ищем сделку с такой же ссылкой.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import Settings

log = logging.getLogger(__name__)


class AmoClient:
    """Минимальный клиент amoCRM: поиск дублей + создание сделок."""

    def __init__(self, settings: Settings, session: Optional[Any] = None):
        self.settings = settings
        if session is not None:
            self.http = session
        else:
            import requests

            self.http = requests.Session()
            self.http.headers.update(
                {
                    "Authorization": f"Bearer {settings.amo_access_token}",
                    "Content-Type": "application/json",
                }
            )

    def _url(self, path: str) -> str:
        return f"{self.settings.amo_base_url}/api/v4/{path.lstrip('/')}"

    def find_lead_by_link(self, link: str) -> Optional[dict[str, Any]]:
        """Ищет существующую сделку по ссылке канала (дедуп)."""
        try:
            response = self.http.get(self._url("leads"), params={"query": link}, timeout=30)
            if getattr(response, "status_code", 0) == 204:
                return None
            if getattr(response, "status_code", 0) != 200:
                log.warning("amo: поиск вернул HTTP %s", response.status_code)
                return None
            payload = response.json() or {}
            leads = (payload.get("_embedded") or {}).get("leads") or []
            return leads[0] if leads else None
        except Exception as exc:
            log.warning("amo: ошибка поиска сделки: %s", exc)
            return None

    def build_lead(self, row: Any) -> dict[str, Any]:
        """Собирает тело сделки из строки выгрузки."""
        fields = self.settings.amo_fields
        custom: list[dict[str, Any]] = []

        def add(field_key: str, value: Any) -> None:
            field_id = fields.get(field_key)
            if field_id and value not in (None, ""):
                custom.append(
                    {"field_id": int(field_id), "values": [{"value": str(value)}]}
                )

        add("link", row["link"])
        add("subscribers", row["subscribers"])
        add("er", row["er"])
        add("stream", row["stream"])
        add("score", row["score"])

        lead: dict[str, Any] = {
            "name": row["title"] or f"@{row['username']}",
            "custom_fields_values": custom,
        }
        if self.settings.amo_pipeline_id:
            lead["pipeline_id"] = int(self.settings.amo_pipeline_id)
        if self.settings.amo_status_id:
            lead["status_id"] = int(self.settings.amo_status_id)
        return lead

    def create_leads(self, rows: list[Any]) -> dict[str, int]:
        """Создаёт сделки пачкой, пропуская дубли по ссылке."""
        to_create: list[dict[str, Any]] = []
        skipped = 0

        for row in rows:
            if self.find_lead_by_link(row["link"]):
                skipped += 1
                continue
            to_create.append(self.build_lead(row))

        created = 0
        # amoCRM принимает до 50 сущностей за запрос
        for chunk_start in range(0, len(to_create), 50):
            chunk = to_create[chunk_start : chunk_start + 50]
            try:
                response = self.http.post(self._url("leads"), json=chunk, timeout=60)
                if getattr(response, "status_code", 0) in (200, 201):
                    created += len(chunk)
                else:
                    log.warning("amo: создание вернуло HTTP %s", response.status_code)
            except Exception as exc:
                log.warning("amo: ошибка создания сделок: %s", exc)

        return {"created": created, "skipped": skipped, "total": len(rows)}


def export_to_amo(
    settings: Settings, rows: list[Any], session: Optional[Any] = None
) -> dict[str, int]:
    if not settings.amo_enabled:
        log.warning("amoCRM не настроен — пропускаем (нужны AMO_BASE_URL, AMO_ACCESS_TOKEN)")
        return {"created": 0, "skipped": 0, "total": 0}
    return AmoClient(settings, session=session).create_leads(rows)
