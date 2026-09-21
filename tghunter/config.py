"""Загрузка конфигурации: .env, streams.yaml, seed-файлы, стоп-маркеры."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

try:  # python-dotenv необязателен, если переменные уже в окружении
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


DEFAULT_CONFIG_DIR = "config"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


@dataclass
class Stream:
    """Пресет стрима из streams.yaml (раздел 6 ТЗ)."""

    name: str
    enabled: bool = True
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["ru"])
    methods: list[str] = field(default_factory=list)
    min_subscribers: int = 1000
    max_subscribers: int = 100000
    min_er: float = 3.0
    min_avg_reactions: float = 5.0
    max_days_since_post: int = 14
    min_posts_30d: int = 4
    seed_file: str = "seed_channels.txt"
    addlist_file: str = "addlists.txt"
    mention_depth: int = 1
    mention_posts_per_channel: int = 50
    keyword_results_per_query: int = 30
    mode: str = "normal"
    separate_export: bool = False
    extra_blacklist: list[str] = field(default_factory=list)
    max_new_channels: int = 200

    @property
    def longlist_mode(self) -> bool:
        """В режиме longlist пороги живости/ER не отсекают канал."""
        return self.mode == "longlist"


@dataclass
class Settings:
    """Значения из .env (раздел 10 ТЗ)."""

    api_id: int = 0
    api_hash: str = ""
    session_name: str = "hunter"
    session_dir: str = "sessions"

    rate_min_interval: float = 2.0
    rate_max_interval: float = 3.0
    max_channels_per_run: int = 200
    method_pause_min: float = 30.0
    method_pause_max: float = 60.0
    floodwait_abort_streak: int = 2

    db_path: str = "channels.db"
    export_dir: str = "exports"
    config_dir: str = DEFAULT_CONFIG_DIR

    bot_token: str = ""
    summary_chat_id: str = ""

    amo_base_url: str = ""
    amo_access_token: str = ""
    amo_pipeline_id: str = ""
    amo_status_id: str = ""
    amo_fields: dict[str, str] = field(default_factory=dict)

    sheets_credentials_file: str = ""
    sheets_spreadsheet_id: str = ""
    sheets_worksheet_prefix: str = "hunting"

    @property
    def session_path(self) -> str:
        return str(Path(self.session_dir) / self.session_name)

    @property
    def summary_enabled(self) -> bool:
        return bool(self.bot_token and self.summary_chat_id)

    @property
    def amo_enabled(self) -> bool:
        return bool(self.amo_base_url and self.amo_access_token)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.sheets_credentials_file and self.sheets_spreadsheet_id)


def load_settings(env_file: Optional[str] = None) -> Settings:
    """Читает .env (если есть) и собирает Settings."""
    load_dotenv(env_file or ".env", override=False)
    return Settings(
        api_id=_env_int("TG_API_ID", 0),
        api_hash=_env("TG_API_HASH"),
        session_name=_env("TG_SESSION_NAME", "hunter"),
        session_dir=_env("TG_SESSION_DIR", "sessions"),
        rate_min_interval=_env_float("RATE_MIN_INTERVAL", 2.0),
        rate_max_interval=_env_float("RATE_MAX_INTERVAL", 3.0),
        max_channels_per_run=_env_int("MAX_CHANNELS_PER_RUN", 200),
        method_pause_min=_env_float("METHOD_PAUSE_MIN", 30.0),
        method_pause_max=_env_float("METHOD_PAUSE_MAX", 60.0),
        floodwait_abort_streak=_env_int("FLOODWAIT_ABORT_STREAK", 2),
        db_path=_env("DB_PATH", "channels.db"),
        export_dir=_env("EXPORT_DIR", "exports"),
        config_dir=_env("CONFIG_DIR", DEFAULT_CONFIG_DIR),
        bot_token=_env("TG_BOT_TOKEN"),
        summary_chat_id=_env("TG_SUMMARY_CHAT_ID"),
        amo_base_url=_env("AMO_BASE_URL").rstrip("/"),
        amo_access_token=_env("AMO_ACCESS_TOKEN"),
        amo_pipeline_id=_env("AMO_PIPELINE_ID"),
        amo_status_id=_env("AMO_STATUS_ID"),
        amo_fields={
            "link": _env("AMO_FIELD_LINK"),
            "subscribers": _env("AMO_FIELD_SUBSCRIBERS"),
            "er": _env("AMO_FIELD_ER"),
            "stream": _env("AMO_FIELD_STREAM"),
            "score": _env("AMO_FIELD_SCORE"),
        },
        sheets_credentials_file=_env("SHEETS_CREDENTIALS_FILE"),
        sheets_spreadsheet_id=_env("SHEETS_SPREADSHEET_ID"),
        sheets_worksheet_prefix=_env("SHEETS_WORKSHEET_PREFIX", "hunting"),
    )


_STREAM_FIELDS = set(Stream.__dataclass_fields__)


def load_streams(path: str | Path) -> dict[str, Stream]:
    """Читает streams.yaml: defaults + список пресетов."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл пресетов: {path}")

    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults: dict[str, Any] = data.get("defaults") or {}
    streams: dict[str, Stream] = {}

    for raw in data.get("streams") or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError(f"Пресет без имени в {path}: {raw!r}")
        merged = {**defaults, **raw}
        unknown = set(merged) - _STREAM_FIELDS
        if unknown:
            raise ValueError(
                f"Неизвестные поля пресета {merged['name']}: {sorted(unknown)}"
            )
        stream = Stream(**merged)
        if stream.name in streams:
            raise ValueError(f"Дубль пресета в {path}: {stream.name}")
        streams[stream.name] = stream

    if not streams:
        raise ValueError(f"В {path} нет ни одного пресета")
    return streams


def read_list_file(path: str | Path) -> list[str]:
    """Читает список строк, игнорируя пустые строки и комментарии (#)."""
    path = Path(path)
    if not path.exists():
        return []
    items: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def load_blacklist(config_dir: str | Path, extra: Optional[list[str]] = None) -> list[str]:
    """Стоп-маркеры из blacklist_markers.txt плюс extra_blacklist пресета."""
    markers = read_list_file(Path(config_dir) / "blacklist_markers.txt")
    if extra:
        markers.extend(extra)
    # дедуп с сохранением порядка
    seen: set[str] = set()
    result: list[str] = []
    for marker in markers:
        low = marker.strip().lower()
        if low and low not in seen:
            seen.add(low)
            result.append(low)
    return result
