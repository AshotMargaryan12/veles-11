"""CLI квалификатора (раздел 13 ТЗ).

    qualify <link> [<link> ...] [--geo XX] [--type individual|institutional]
            [--notes "..."] [--json] [--csv FILE] [--refresh] [--no-llm]
    qualify batch <file> [--csv out.csv]
    qualify history [--days 30]
    qualify config check [--offline]
    qualify login
    qualify web
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import ConfigError, Settings, load_config, load_settings
from .links import LinkError, parse_batch_line
from .models import PartnerRequest
from .render import (
    card_csv_row,
    csv_text,
    render_json,
    render_markdown,
    skipped_csv_row,
    sort_key,
    write_csv,
)
from .service import BudgetExceeded, Qualifier
from .store import Store

log = logging.getLogger("qualifier")

COMMANDS = {"check", "batch", "history", "config", "login", "web"}
_GLOBAL_WITH_VALUE = {"--env", "--config-dir", "--db"}
_GLOBAL_FLAGS = {"--demo", "-v", "--verbose"}


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _add_qualify_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--geo", help="гео аудитории, если известно менеджеру (ISO2, напр. EG)")
    parser.add_argument("--type", dest="affiliate_type", default="individual",
                        help="тип партнёра из criteria.yaml: individual | institutional")
    parser.add_argument("--refresh", action="store_true", help="не брать данные из кэша")
    parser.add_argument("--no-llm", action="store_true", help="без LLM-слоя (только локальные эвристики)")
    parser.add_argument("--no-save", action="store_true", help="не сохранять карточку в reports/")
    parser.add_argument("--lang", choices=["en", "ru"], help="язык черновика заявки (по умолчанию DRAFT_LANGUAGE)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qualify",
        description="Квалификатор партнёра: ссылка на площадку блогера -> карточка квалификации",
    )
    parser.add_argument("--env", default=".env", help="файл с ключами (по умолчанию .env)")
    parser.add_argument("--config-dir", help="каталог criteria.yaml и др. (по умолчанию config/qualifier)")
    parser.add_argument("--db", help="SQLite-база кэша и истории (по умолчанию qualifier.db)")
    parser.add_argument("--demo", action="store_true",
                        help="вымышленные каналы и ответы LLM: без сети, сессии и ключей")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="карточка по ссылкам одного партнёра (команда по умолчанию)")
    check.add_argument("links", nargs="+", help="ссылки на площадки партнёра")
    check.add_argument("--notes", default="", help="заметки менеджера — попадут в черновик заявки")
    check.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    check.add_argument("--csv", help="дописать строку карточки в CSV-файл")
    _add_qualify_options(check)

    batch = sub.add_parser("batch", help="пакетный режим: файл со списком партнёров")
    batch.add_argument("file", help="одна строка — один партнёр: ссылки и geo=.. type=.. notes=\"..\"")
    batch.add_argument("--csv", help="куда сохранить таблицу (по умолчанию reports/batch_<дата>.csv)")
    batch.add_argument("--json", action="store_true", help="вывести все карточки в JSON")
    _add_qualify_options(batch)

    history = sub.add_parser("history", help="что уже проверялось")
    history.add_argument("--days", type=int, default=30)
    history.add_argument("--json", action="store_true")

    config = sub.add_parser("config", help="проверка конфигов и доступов")
    config.add_argument("action", choices=["check"])
    config.add_argument("--offline", action="store_true", help="не проверять доступы по сети")

    sub.add_parser("login", help="создать сессию Telegram (один раз)")

    web = sub.add_parser("web", help="локальный веб-интерфейс на 127.0.0.1")
    web.add_argument("--port", type=int, default=8766)
    web.add_argument("--no-browser", action="store_true")
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """`qualify t.me/x` -> `qualify check t.me/x`: команда по умолчанию — проверка."""
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _GLOBAL_WITH_VALUE:
            i += 2
            continue
        if token in _GLOBAL_FLAGS:
            i += 1
            continue
        if token in ("-h", "--help") or token in COMMANDS:
            return argv
        # первый не-глобальный аргумент: дальше идут ссылки и опции проверки
        return argv[:i] + ["check"] + argv[i:]
    return argv


# --------------------------------------------------------------------------
# Сборка зависимостей
# --------------------------------------------------------------------------

def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.env)
    if args.config_dir:
        settings.config_dir = args.config_dir
    if args.db:
        settings.db_path = args.db
    if getattr(args, "lang", None):
        settings.draft_language = args.lang
    if args.demo:
        settings.db_path = args.db or ":memory:"
    return settings


def build_qualifier(args: argparse.Namespace, settings: Settings) -> Qualifier:
    config = load_config(settings)
    store = Store(settings.db_path, ttl_days=settings.cache_ttl_days)
    use_llm = not getattr(args, "no_llm", False)
    if args.demo:
        from .demo import DemoAnthropic, demo_collectors
        from .llm import LLMClient

        return Qualifier(
            settings, config, store=store, collectors=demo_collectors(),
            llm=LLMClient(settings, client=DemoAnthropic(), cache=store), use_llm=use_llm,
        )
    return Qualifier(settings, config, store=store, use_llm=use_llm)


def _print(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    try:
        qualifier = build_qualifier(args, settings)
    except ConfigError as exc:
        _err(f"Ошибка конфигурации: {exc}")
        return 2
    request = PartnerRequest(
        links=args.links, geo=args.geo, affiliate_type=args.affiliate_type,
        notes=args.notes, refresh=args.refresh,
    )
    try:
        card = qualifier.run(request)
    except LinkError as exc:
        _err(f"Ссылка: {exc}")
        return 2
    except ConfigError as exc:
        _err(f"Ошибка конфигурации: {exc}")
        return 2
    except BudgetExceeded as exc:
        _err(str(exc))
        return 3
    except _run_aborted() as exc:
        _err(f"Telegram остановил прогон: {exc}")
        return 3
    finally:
        qualifier.close()

    qualifier.save(card, write_report=not args.no_save)
    if args.json:
        _print(render_json(card))
    else:
        template = _card_template(qualifier)
        _print(render_markdown(card, template))
        if card.report_path:
            _print(f"Карточка сохранена: {card.report_path}")
    if args.csv:
        path = Path(args.csv)
        rows = [card_csv_row(card)]
        if path.exists() and path.stat().st_size:
            _append_csv(path, rows)
        else:
            write_csv(rows, path)
        _err(f"Строка добавлена в {path}")
    return 0


def _card_template(qualifier: Qualifier) -> Optional[str]:
    try:
        return qualifier.config.template("card.md")
    except ConfigError:
        return None


def _append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    text = csv_text(rows)
    body = text.split("\n", 1)[1] if "\n" in text else ""
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(body)


def _run_aborted() -> type[BaseException]:
    try:
        from tghunter.ratelimit import RunAborted
    except ImportError:  # pragma: no cover
        return RuntimeError
    return RunAborted


def cmd_batch(args: argparse.Namespace) -> int:
    settings = _settings(args)
    path = Path(args.file)
    if not path.exists():
        _err(f"Нет файла {path}")
        return 2
    try:
        qualifier = build_qualifier(args, settings)
    except ConfigError as exc:
        _err(f"Ошибка конфигурации: {exc}")
        return 2

    defaults = PartnerRequest(
        links=[], geo=args.geo, affiliate_type=args.affiliate_type, refresh=args.refresh,
    )
    requests: list[PartnerRequest] = []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            request = parse_batch_line(line, defaults)
        except LinkError as exc:
            rows.append(skipped_csv_row([line.strip()], f"строка {number}: {exc}"))
            continue
        if request is not None:
            requests.append(request)

    cards = []
    total = len(requests)
    stopped: Optional[str] = None
    try:
        for index, request in enumerate(requests, 1):
            if stopped:
                rows.append(skipped_csv_row(request.links, f"пропущен: {stopped}"))
                continue
            fetches_before = qualifier.fetches
            _err(f"[{index}/{total}] {' '.join(request.links)}")
            try:
                card = qualifier.run(request)
            except LinkError as exc:
                rows.append(skipped_csv_row(request.links, f"ссылка: {exc}"))
                continue
            except BudgetExceeded as exc:
                stopped = str(exc)
                rows.append(skipped_csv_row(request.links, f"пропущен: {stopped}"))
                continue
            except _run_aborted() as exc:
                stopped = f"Telegram остановил прогон ({exc})"
                rows.append(skipped_csv_row(request.links, f"пропущен: {stopped}"))
                continue
            except ConfigError as exc:
                rows.append(skipped_csv_row(request.links, f"конфиг: {exc}"))
                continue
            qualifier.save(card, write_report=not args.no_save)
            cards.append(card)
            rows.append(card_csv_row(card))
            # пауза между партнёрами, только если ходили в сеть (раздел 10-11 ТЗ)
            if index < total and qualifier.fetches > fetches_before and not args.demo:
                time.sleep(settings.batch_pause_sec)
    finally:
        qualifier.close()

    out = Path(args.csv) if args.csv else Path(settings.reports_dir) / f"batch_{datetime.now():%Y%m%d_%H%M}.csv"
    write_csv(rows, out)
    if args.json:
        _print(render_json(cards))
    else:
        _print(_batch_table(rows))
    _err(f"Готово. Карточек: {len(cards)}, пропущено: {len(rows) - len(cards)}. CSV: {out}")
    if stopped:
        _err(f"Прогон остановлен: {stopped}")
    return 0


def _batch_table(rows: list[dict[str, Any]]) -> str:
    header = f"{'тир':>4}  {'ER%':>5}  {'подписч.':>9}  {'гео':<6} {'флаги':<28} партнёр"
    lines = [header, "-" * len(header)]
    for row in sorted(rows, key=sort_key):
        if row.get("status") not in ("ok",):
            lines.append(f"{'—':>4}  {'—':>5}  {'—':>9}  {'—':<6} {'—':<28} {row.get('links')}  [{row.get('status')}]")
            continue
        geo = f"{row.get('geo') or '?'}{'*' if row.get('geo_confidence') not in ('high', 'manual') else ''}"
        lines.append(
            f"{row.get('tier') or '—':>4}  {row.get('er') or '—':>5}  {row.get('followers_max') or '—':>9}  "
            f"{geo:<6} {(row.get('flags') or '—')[:28]:<28} {row.get('partner')} ({row.get('handle')})"
        )
    lines.append("* — гео нужно подтвердить у партнёра")
    return "\n".join(lines)


def cmd_history(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = Store(settings.db_path, ttl_days=settings.cache_ttl_days)
    rows = store.history(args.days)
    store.close()
    if args.json:
        import json

        _print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        _print(f"За {args.days} дней проверок не было.")
        return 0
    _print(f"Проверки за {args.days} дней: {len(rows)}")
    for row in rows:
        if row["tier"] is not None:
            tier = f"{row['tier']:g}%"
        elif not row["followers"]:
            tier = "нет данных"
        else:
            tier = "ниже тира"
        er = f"ER {row['er']}%" if row["er"] is not None else "ER —"
        review = " | ручная проверка" if row["manual_review"] else ""
        flags = f" | флаги: {row['flags']}" if row["flags"] else ""
        _print(
            f"{row['created_at'][:16].replace('T', ' ')}  {row['partner']}  — {tier}, "
            f"{row['followers'] or 0} подп., {er}, гео {row['geo'] or '?'} ({row['geo_confidence']})"
            f"{review}{flags}"
        )
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    from .checks import run_checks

    settings = _settings(args)
    results = run_checks(settings, online=not args.offline, env_file=args.env)
    errors = 0
    for status, title, detail in results:
        mark = {"ok": "✓", "warn": "!", "error": "✗"}[status]
        errors += status == "error"
        _print(f"[{mark}] {title}" + (f" — {detail}" if detail else ""))
    _print("Готово к работе." if not errors else f"Ошибок: {errors}")
    return 1 if errors else 0


def cmd_login(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if not settings.telegram_configured:
        _err("Заполните TG_API_ID и TG_API_HASH в .env (https://my.telegram.org)")
        return 1
    from tghunter.config import Settings as HunterSettings
    from tghunter.ratelimit import RateLimiter
    from tghunter.tg import TelegramGateway, TelegramUnavailable

    hunter = HunterSettings(
        api_id=settings.tg_api_id, api_hash=settings.tg_api_hash,
        session_name=settings.tg_session_name, session_dir=settings.tg_session_dir,
    )
    try:
        gateway = TelegramGateway(hunter, RateLimiter())
    except TelegramUnavailable as exc:
        _err(f"Ошибка: {exc}")
        return 1
    client = gateway.client
    _print(f"Создаём сессию: {settings.session_file}")
    _print("ВАЖНО: используйте ОТДЕЛЬНЫЙ юзер-аккаунт, не личный и не основной рабочий.")
    client.start()  # Telethon сам спросит телефон, код и пароль 2FA
    me = client.loop.run_until_complete(client.get_me())
    _print(f"Авторизован: {getattr(me, 'username', None) or getattr(me, 'first_name', '')}")
    gateway.disconnect()
    try:
        os.chmod(settings.session_file, 0o600)
    except OSError:
        pass
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        from .web import serve
    except ImportError as exc:
        _err(f"Для веб-интерфейса нужны fastapi и uvicorn: pip install fastapi uvicorn ({exc})")
        return 1
    return serve(args, port=args.port, open_browser=not args.no_browser)


HANDLERS = {
    "check": cmd_check,
    "batch": cmd_batch,
    "history": cmd_history,
    "config": cmd_config,
    "login": cmd_login,
    "web": cmd_web,
}


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv))
    setup_logging(args.verbose)
    if not args.command:
        parser.print_help()
        return 0
    return HANDLERS[args.command](args)
