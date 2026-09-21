"""CSV-выгрузка лонглиста (раздел 8.1 ТЗ).

Каналы без контакта выгружаются отдельным файлом (`*_no_contact.csv`).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

# (колонка в CSV, ключ в строке БД)
EXPORT_COLUMNS: Sequence[tuple[str, str]] = (
    ("username", "username"),
    ("ссылка", "link"),
    ("title", "title"),
    ("подписчики", "subscribers"),
    ("ER_%", "er"),
    ("медиана_просмотров", "median_views"),
    ("среднее_просмотров", "avg_views"),
    ("дней_с_последнего_поста", "days_since_last_post"),
    ("постов_за_30д", "posts_last_30d"),
    ("среднее_реакций", "avg_reactions"),
    ("комментарии", "comments_enabled"),
    ("язык", "language"),
    ("монетизация", "monetized"),
    ("маркеры_монетизации", "monetization_markers"),
    ("контакт", "contact"),
    ("score", "score"),
    ("разбивка_score", "score_breakdown"),
    ("метод", "method"),
    ("источник", "source_channel"),
    ("стрим", "stream"),
    ("статус", "status"),
    ("первая_находка", "first_seen"),
    ("последняя_проверка", "last_checked"),
)

_BOOL_COLUMNS = {"comments_enabled", "monetized"}


def _cell(row: Any, key: str) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = None
    if key in _BOOL_COLUMNS:
        return "да" if value else "нет"
    return "" if value is None else value


def write_rows(rows: Iterable[Any], path: str | Path) -> int:
    """Пишет строки в CSV, возвращает количество записей."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # utf-8-sig — чтобы Excel открывал кириллицу без плясок
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([title for title, _ in EXPORT_COLUMNS])
        for row in rows:
            writer.writerow([_cell(row, key) for _, key in EXPORT_COLUMNS])
            count += 1
    log.info("CSV: %d строк -> %s", count, path)
    return count


def export_csv(rows: Iterable[Any], path: str | Path) -> dict[str, Any]:
    """Делит строки на «с контактом» и «без контакта» и пишет два файла."""
    rows = list(rows)
    with_contact = [r for r in rows if _cell(r, "contact")]
    without_contact = [r for r in rows if not _cell(r, "contact")]

    path = Path(path)
    result: dict[str, Any] = {
        "main_file": str(path),
        "main_count": write_rows(with_contact, path),
        "no_contact_file": None,
        "no_contact_count": 0,
        "total": len(rows),
    }

    if without_contact:
        nc_path = path.with_name(f"{path.stem}_no_contact{path.suffix}")
        result["no_contact_file"] = str(nc_path)
        result["no_contact_count"] = write_rows(without_contact, nc_path)

    return result
