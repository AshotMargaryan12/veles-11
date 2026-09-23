"""Хранение ключей сервисов в .env.

Ключи лежат только в .env рядом с проектом, права выставляются в 600, файл в
.gitignore. Наружу (в лог, в UI, в ответ сервера) значение никогда не уходит
целиком — только маска вида `1234…cdef`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# Поля, которые умеет редактировать UI настроек и команда setup
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("TG_API_ID", "Telegram api_id", "число с my.telegram.org"),
    ("TG_API_HASH", "Telegram api_hash", "32 символа с my.telegram.org"),
    ("TGSTAT_TOKEN", "Ключ TGStat", "личный кабинет api.tgstat.ru"),
    ("TG_BOT_TOKEN", "Токен бота для сводок", "необязательно, от @BotFather"),
    ("TG_SUMMARY_CHAT_ID", "Чат для сводок", "необязательно, id чата или канала"),
    ("AMO_BASE_URL", "Адрес amoCRM", "необязательно, https://ваш-аккаунт.amocrm.ru"),
    ("AMO_ACCESS_TOKEN", "Токен amoCRM", "необязательно, долгосрочный токен"),
    ("SHEETS_CREDENTIALS_FILE", "Файл сервисного аккаунта Google", "необязательно, путь к json"),
    ("SHEETS_SPREADSHEET_ID", "ID таблицы Google", "необязательно"),
)

SECRET_FIELDS = {
    "TG_API_HASH", "TGSTAT_TOKEN", "TG_BOT_TOKEN", "AMO_ACCESS_TOKEN",
}

_LINE_RE = re.compile(r"^\s*([A-Z0-9_]+)\s*=(.*)$")


def mask(value: str) -> str:
    """`abcdef0123456789` -> `abcd…6789`. Пустое значение — пустая строка."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return value[0] + "…" * (len(value) > 1)
    return f"{value[:4]}…{value[-4:]}"


def read_env(path: str | Path = ".env") -> dict[str, str]:
    """Читает .env в словарь. Комментарии и мусорные строки игнорируются."""
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        values[key] = raw
    return values


def write_env(updates: dict[str, str], path: str | Path = ".env") -> list[str]:
    """Обновляет .env, сохраняя комментарии и порядок строк.

    Пустое значение в updates удаляет ключ. Возвращает список изменённых ключей.
    Файл создаётся с правами 600.
    """
    path = Path(path)
    existing_lines = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    remaining = dict(updates)
    changed: list[str] = []
    result: list[str] = []

    for line in existing_lines:
        match = _LINE_RE.match(line)
        if not match or match.group(1) not in remaining:
            result.append(line)
            continue
        key = match.group(1)
        new_value = remaining.pop(key).strip()
        old_value = read_env(path).get(key, "")
        if new_value != old_value:
            changed.append(key)
        result.append(f"{key}={new_value}")

    for key, value in remaining.items():
        value = value.strip()
        if not value:
            continue
        result.append(f"{key}={value}")
        changed.append(key)

    path.write_text("\n".join(result).rstrip("\n") + "\n", encoding="utf-8")
    _harden(path)
    return changed


def _harden(path: Path) -> None:
    """Права 600: ключи не должны читаться кем попало."""
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover — Windows и экзотические ФС
        pass


def apply_to_environ(values: dict[str, str]) -> None:
    """Прокидывает значения в os.environ, чтобы load_settings их увидел."""
    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def current_values(path: str | Path = ".env") -> dict[str, str]:
    """Текущие значения: сначала .env, затем переменные окружения."""
    values = read_env(path)
    for key, _title, _hint in FIELDS:
        env_value = os.getenv(key)
        if env_value and not values.get(key):
            values[key] = env_value
    return values


def masked_values(path: str | Path = ".env") -> dict[str, str]:
    """То, что безопасно показать в интерфейсе."""
    values = current_values(path)
    return {
        key: (mask(values.get(key, "")) if key in SECRET_FIELDS else values.get(key, ""))
        for key, _title, _hint in FIELDS
    }
