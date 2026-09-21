"""Выгрузка в Google Sheets (раздел 8 ТЗ, опционально, флаг --sheets).

Требует `pip install gspread google-auth` и сервисный аккаунт с доступом
к таблице SHEETS_SPREADSHEET_ID.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import Settings
from .csv_export import EXPORT_COLUMNS, _cell

log = logging.getLogger(__name__)


def export_to_sheets(
    settings: Settings,
    rows: list[Any],
    worksheet_name: str,
    client: Optional[Any] = None,
) -> dict[str, Any]:
    """Пишет строки на отдельный лист таблицы. Перезаписывает лист целиком."""
    if not settings.sheets_enabled:
        log.warning(
            "Google Sheets не настроен — пропускаем "
            "(нужны SHEETS_CREDENTIALS_FILE, SHEETS_SPREADSHEET_ID)"
        )
        return {"written": 0, "worksheet": worksheet_name}

    if client is None:
        try:
            import gspread
        except ImportError:
            log.warning("Нет gspread — выгрузка в Sheets пропущена "
                        "(pip install gspread google-auth)")
            return {"written": 0, "worksheet": worksheet_name}
        client = gspread.service_account(filename=settings.sheets_credentials_file)

    header = [title for title, _ in EXPORT_COLUMNS]
    values = [header]
    for row in rows:
        values.append([str(_cell(row, key)) for _, key in EXPORT_COLUMNS])

    try:
        spreadsheet = client.open_by_key(settings.sheets_spreadsheet_id)
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            worksheet.clear()
        except Exception:
            worksheet = spreadsheet.add_worksheet(
                title=worksheet_name, rows=max(len(values) + 10, 100), cols=len(header)
            )
        worksheet.update(values, "A1")
        log.info("Sheets: %d строк -> лист %s", len(rows), worksheet_name)
        return {"written": len(rows), "worksheet": worksheet_name}
    except Exception as exc:
        log.warning("Sheets: ошибка выгрузки: %s", exc)
        return {"written": 0, "worksheet": worksheet_name, "error": str(exc)}
