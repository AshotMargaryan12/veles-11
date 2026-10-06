"""`qualify config check`: конфиги, шаблоны и доступы к площадкам и LLM.

Секреты не печатаются — только «задан / не задан» и результат проверки.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable

from .config import ConfigError, Settings, load_cpa, load_criteria, load_geo_profiles, load_thresholds
from .draft import _FIELD_RE, parse_template

Result = tuple[str, str, str]  # (ok|warn|error, что, детали)

KNOWN_FIELDS = {
    "PARTNER_NAME", "PARTNER_TYPE", "LINKS", "CHECK_DATE", "METRICS", "AUDIENCE_LINE",
    "GEO_LINE", "TIER_LINE", "CRITERIA_LINE", "CPA_LINE", "DEAL_STRUCTURE", "REVIEW_TERMS",
    "RISKS_LIST", "MANAGER_NOTES", "REQUEST_LINE", "VOLUME_LINE", "ROI_LINE",
}


def _safe(results: list[Result], title: str, fn: Callable[[], Result]) -> None:
    try:
        results.append(fn())
    except Exception as exc:  # одна проверка не должна ронять остальные
        results.append(("error", title, str(exc)[:200]))


def check_files(settings: Settings, env_file: str = ".env") -> list[Result]:
    results: list[Result] = []
    config_dir = Path(settings.config_dir)

    results.append(
        ("ok", f"файл ключей {env_file}", "найден") if Path(env_file).exists()
        else ("warn", f"файл ключей {env_file}", "не найден — скопируйте .env.example в .env")
    )

    def criteria() -> Result:
        c = load_criteria(config_dir)
        types = ", ".join(f"{name} (тиров: {len(t.tiers)})" for name, t in c.affiliate_types.items())
        if c.is_example:
            return ("warn", "criteria.yaml",
                    f"нет реального файла — работают ПРИМЕРЫ-ЗАГЛУШКИ ({c.path}). Типы: {types}")
        return ("ok", "criteria.yaml", f"{c.path}: {types}")

    _safe(results, "criteria.yaml", criteria)

    def cpa() -> Result:
        name = "cpa_by_country.csv"
        try:
            name = load_criteria(config_dir).cpa_rates_file
        except ConfigError:
            pass
        table = load_cpa(config_dir, name)
        if table.path is None:
            return ("error", name, f"нет файла в {config_dir}")
        countries = sum(1 for r in table.rates if r.country != "*")
        regions = sum(1 for r in table.rates if r.country == "*")
        detail = f"{table.path}: стран {countries}, региональных ставок {regions}"
        if table.is_example:
            return ("warn", name, "нет реального файла — работают ПРИМЕРЫ-ЗАГЛУШКИ. " + detail)
        return ("ok", name, detail)

    _safe(results, "cpa_by_country.csv", cpa)

    def thresholds() -> Result:
        th = load_thresholds(config_dir)
        if th.path is None:
            return ("warn", "thresholds.yaml", "нет файла — используются значения по умолчанию")
        return ("ok", "thresholds.yaml", str(th.path))

    _safe(results, "thresholds.yaml", thresholds)

    def volume() -> Result:
        from .config import load_volume_criteria

        vc = load_volume_criteria(config_dir)
        if vc is None:
            return ("warn", "volume_criteria.yaml", "нет файла — блок объёмов и ROI выключен")
        detail = (f"{vc.path}: тиров spot {len(vc.markets['spot'].tiers)}, futures "
                  f"{len(vc.markets['futures'].tiers)}, правил whitelisting {len(vc.eligibility)}")
        if vc.is_example:
            return ("warn", "volume_criteria.yaml",
                    "нет реального файла — работают ПРИМЕРЫ-ЗАГЛУШКИ (qualify config import). " + detail)
        return ("ok", "volume_criteria.yaml", detail)

    _safe(results, "volume_criteria.yaml", volume)

    def geo() -> Result:
        profiles = load_geo_profiles(config_dir)
        if not profiles:
            return ("error", "geo_markers.yaml", "нет словаря гео — гео не будет определяться")
        missing_region = [c for c, p in profiles.items() if not p.region]
        detail = f"стран: {len(profiles)}"
        if missing_region:
            return ("warn", "geo_markers.yaml", f"{detail}; без region: {', '.join(missing_region)}")
        return ("ok", "geo_markers.yaml", detail)

    _safe(results, "geo_markers.yaml", geo)

    templates = Path(settings.templates_dir)
    for name in ("whitelist_request.md", f"whitelist_request.{settings.draft_language}.md", "card.md"):
        path = templates / name
        if not path.exists():
            if name == "whitelist_request.md":
                results.append(("error", f"шаблон {name}", f"нет файла {path}"))
            continue
        body, slots = parse_template(path.read_text(encoding="utf-8"))
        unknown = sorted(set(_FIELD_RE.findall(body)) - KNOWN_FIELDS) if name != "card.md" else []
        if unknown:
            results.append(("warn", f"шаблон {name}", f"неизвестные поля: {', '.join(unknown)}"))
        else:
            detail = f"LLM-блоков: {len(slots)}" if name != "card.md" else "ок"
            results.append(("ok", f"шаблон {name}", detail))

    gitignore = Path(".gitignore")
    if gitignore.exists():
        text = gitignore.read_text(encoding="utf-8")
        missing = [p for p in ("criteria.yaml", "cpa_by_country.csv", "volume_criteria.yaml", "*.session", ".env")
                   if p not in text]
        results.append(
            ("ok", ".gitignore", "сессия, ключи и внутренние ставки исключены") if not missing
            else ("error", ".gitignore", f"не исключены: {', '.join(missing)}")
        )
    return results


def check_access(settings: Settings, online: bool = True) -> list[Result]:
    results: list[Result] = []

    # --- Telegram -----------------------------------------------------------
    if not settings.telegram_configured:
        results.append(("warn", "Telegram", "не заданы TG_API_ID / TG_API_HASH — Telegram не опрашивается"))
    elif not settings.session_file.exists():
        results.append(("warn", "Telegram", f"нет сессии {settings.session_file} — выполните: qualify login"))
    elif not online:
        results.append(("ok", "Telegram", f"ключи заданы, сессия {settings.session_file} (без проверки по сети)"))
    else:
        def telegram() -> Result:
            from tghunter.config import Settings as HunterSettings
            from tghunter.ratelimit import RateLimiter
            from tghunter.tg import TelegramGateway, TelegramUnavailable

            hunter = HunterSettings(
                api_id=settings.tg_api_id, api_hash=settings.tg_api_hash,
                session_name=settings.tg_session_name, session_dir=settings.tg_session_dir,
            )
            try:
                gateway = TelegramGateway(hunter, RateLimiter())
                gateway.connect()
            except TelegramUnavailable as exc:
                return ("error", "Telegram", str(exc))
            gateway.disconnect()
            return ("ok", "Telegram", "сессия авторизована")

        _safe(results, "Telegram", telegram)

    # --- YouTube -------------------------------------------------------------
    has_ytdlp = importlib.util.find_spec("yt_dlp") is not None
    if settings.yt_api_key and online:
        def youtube() -> Result:
            import requests

            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id", "id": "UC_x5XG1OV2P6uZZ5FSM9Ttw", "key": settings.yt_api_key},
                timeout=15,
            )
            if response.status_code == 200:
                return ("ok", "YouTube Data API", "ключ принят (1 единица квоты)")
            return ("error", "YouTube Data API", f"ответ {response.status_code}: {response.text[:120]}")

        _safe(results, "YouTube Data API", youtube)
    elif settings.yt_api_key:
        results.append(("ok", "YouTube Data API", "ключ задан (без проверки по сети)"))
    elif has_ytdlp:
        results.append(("warn", "YouTube", "нет YT_API_KEY — работает yt-dlp: цифры округлены, нет страны"))
    else:
        results.append(("warn", "YouTube", "нет YT_API_KEY и не установлен yt-dlp — YouTube недоступен"))

    # --- X -------------------------------------------------------------------
    if not settings.x_bearer_token:
        results.append(("warn", "X / Twitter", "нет X_BEARER_TOKEN — по X будет «данные недоступны»"))
    elif online:
        def x() -> Result:
            import requests

            response = requests.get(
                "https://api.x.com/2/users/by/username/X",
                headers={"Authorization": f"Bearer {settings.x_bearer_token}"},
                timeout=15,
            )
            if response.status_code == 200:
                return ("ok", "X API", "токен принят")
            return ("warn", "X API", f"ответ {response.status_code} — данные X будут недоступны")

        _safe(results, "X API", x)
    else:
        results.append(("ok", "X API", "токен задан (без проверки по сети)"))

    # --- Anthropic -----------------------------------------------------------
    if not settings.llm_configured:
        results.append(("warn", "Anthropic API", "нет ANTHROPIC_API_KEY — карточки без LLM-оценки"))
    elif online:
        def anthropic_check() -> Result:
            import anthropic

            client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
            try:
                model = client.models.retrieve(settings.llm_model)
            except anthropic.AuthenticationError:
                return ("error", "Anthropic API", "ключ отклонён (401)")
            except anthropic.NotFoundError:
                return ("error", "Anthropic API", f"модель {settings.llm_model} недоступна — проверьте LLM_MODEL")
            return ("ok", "Anthropic API", f"ключ принят, модель {getattr(model, 'id', settings.llm_model)}")

        _safe(results, "Anthropic API", anthropic_check)
    else:
        results.append(("ok", "Anthropic API", f"ключ задан, модель {settings.llm_model} (без проверки по сети)"))
    return results


def run_checks(settings: Settings, online: bool = True, env_file: str = ".env") -> list[Result]:
    return check_files(settings, env_file) + check_access(settings, online)


def summary(results: list[Result]) -> dict[str, Any]:
    return {
        "errors": sum(1 for r in results if r[0] == "error"),
        "warnings": sum(1 for r in results if r[0] == "warn"),
    }
