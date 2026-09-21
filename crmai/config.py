"""Конфигурация ИИ-анализатора: всё из .env (раздел 7 ТЗ).

Ключи и ID в коде не хранятся. Любое значение можно переопределить переменной
окружения — файл .env нужен только для удобства локального запуска.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # python-dotenv необязателен, если переменные уже в окружении
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


# Модель из ТЗ. claude-sonnet-5 — текущее поколение Sonnet: дешевле и сильнее,
# меняется одной строкой в .env (LLM_MODEL), код от версии не зависит.
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"
DEFAULT_TZ = "Europe/Moscow"

# Сколько сообщений максимум уходит в один запрос к LLM (раздел 4.3 ТЗ)
MAX_DIALOG_MESSAGES = 60
# Сколько сообщений контекста подмешивается к исходящим в контроле качества
QUALITY_CONTEXT_MESSAGES = 5


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").strip().lower()
    return value in ("1", "true", "yes", "y", "on", "да")


def _env_ids(name: str) -> list[int]:
    """`PIPELINE_IDS=123,456` -> [123, 456]. Мусор молча отбрасывается."""
    ids: list[int] = []
    for chunk in _env(name).replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            continue
    return ids


@dataclass
class Settings:
    """Значения из .env (раздел 7 ТЗ)."""

    # --- Wazzup ---
    wazzup_api_key: str = ""
    # Необязательный общий секрет: если задан, вебхук без него отдаёт 403
    wazzup_webhook_secret: str = ""

    # --- amoCRM ---
    amo_domain: str = ""
    amo_client_id: str = ""
    amo_client_secret: str = ""
    amo_access_token: str = ""
    amo_refresh_token: str = ""
    amo_redirect_uri: str = ""
    amo_manager_user_id: int = 0
    pipeline_ids: list[int] = field(default_factory=list)
    # Куда складывать обновлённую пару токенов после авто-refresh
    amo_token_file: str = "amo_tokens.json"

    # --- LLM ---
    anthropic_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    max_llm_calls_per_day: int = 200

    # --- Telegram ---
    tg_bot_token: str = ""
    tg_chat_ashot: str = ""
    tg_chat_danil: str = ""
    send_to_manager: bool = False

    # --- Расписание и хранилище ---
    batch_hour: int = 19
    tz: str = DEFAULT_TZ
    db_path: str = "crm_analyzer.db"
    prompts_dir: str = "prompts"

    # --- Веб-сервер вебхуков ---
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    @property
    def amo_base_url(self) -> str:
        return f"https://{self.amo_domain}.amocrm.ru" if self.amo_domain else ""

    @property
    def amo_enabled(self) -> bool:
        return bool(self.amo_domain and self.amo_access_token)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.tg_bot_token and self.tg_chat_ashot)

    @property
    def alert_chats(self) -> list[str]:
        """Кому уходят алерты и дайджесты: руководитель всегда, менеджер — по флагу."""
        chats = [self.tg_chat_ashot] if self.tg_chat_ashot else []
        if self.send_to_manager and self.tg_chat_danil:
            chats.append(self.tg_chat_danil)
        return chats

    def lead_url(self, lead_id: int | str) -> str:
        """Ссылка на карточку сделки — идёт в алерты и дайджест."""
        if not self.amo_domain or not lead_id:
            return ""
        return f"{self.amo_base_url}/leads/detail/{lead_id}"

    def missing_required(self) -> list[str]:
        """Чего не хватает для боевого запуска. Пустой список — можно стартовать."""
        missing: list[str] = []
        if not self.amo_domain:
            missing.append("AMO_DOMAIN")
        if not self.amo_access_token:
            missing.append("AMO_ACCESS_TOKEN")
        if not self.amo_manager_user_id:
            missing.append("AMO_MANAGER_USER_ID")
        if not self.pipeline_ids:
            missing.append("PIPELINE_IDS")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.tg_bot_token:
            missing.append("TG_BOT_TOKEN")
        if not self.tg_chat_ashot:
            missing.append("TG_CHAT_ASHOT")
        return missing


def load_settings(env_file: Optional[str] = None) -> Settings:
    """Читает .env (если есть) и собирает Settings."""
    load_dotenv(env_file or ".env", override=False)
    return Settings(
        wazzup_api_key=_env("WAZZUP_API_KEY"),
        wazzup_webhook_secret=_env("WAZZUP_WEBHOOK_SECRET"),
        amo_domain=_env("AMO_DOMAIN").replace(".amocrm.ru", "").strip("/ "),
        amo_client_id=_env("AMO_CLIENT_ID"),
        amo_client_secret=_env("AMO_CLIENT_SECRET"),
        amo_access_token=_env("AMO_ACCESS_TOKEN"),
        amo_refresh_token=_env("AMO_REFRESH_TOKEN"),
        amo_redirect_uri=_env("AMO_REDIRECT_URI"),
        amo_manager_user_id=_env_int("AMO_MANAGER_USER_ID", 0),
        pipeline_ids=_env_ids("PIPELINE_IDS"),
        amo_token_file=_env("AMO_TOKEN_FILE", "amo_tokens.json"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        llm_model=_env("LLM_MODEL", DEFAULT_LLM_MODEL),
        max_llm_calls_per_day=_env_int("MAX_LLM_CALLS_PER_DAY", 200),
        tg_bot_token=_env("TG_BOT_TOKEN"),
        tg_chat_ashot=_env("TG_CHAT_ASHOT"),
        tg_chat_danil=_env("TG_CHAT_DANIL"),
        send_to_manager=_env_bool("SEND_TO_MANAGER", False),
        batch_hour=_env_int("BATCH_HOUR", 19),
        tz=_env("TZ", DEFAULT_TZ),
        db_path=_env("CRM_DB_PATH", "crm_analyzer.db"),
        prompts_dir=_env("PROMPTS_DIR", "prompts"),
        webhook_host=_env("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=_env_int("WEBHOOK_PORT", 8080),
    )


def save_amo_tokens(settings: Settings, access: str, refresh: str) -> None:
    """Сохраняет обновлённую пару токенов amo (refresh одноразовый — теряем, ломаем интеграцию)."""
    import json

    path = Path(settings.amo_token_file)
    parent = path.parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": access, "refresh_token": refresh}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover — нестандартные ФС
        pass


def load_amo_tokens(settings: Settings) -> Settings:
    """Подхватывает токены из amo_token_file, если он свежее .env."""
    import json

    path = Path(settings.amo_token_file)
    if not path.exists():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    settings.amo_access_token = data.get("access_token") or settings.amo_access_token
    settings.amo_refresh_token = data.get("refresh_token") or settings.amo_refresh_token
    return settings
