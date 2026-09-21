"""CLI анализатора.

Команды:
    serve                      — вебхук Wazzup + расписание батчей (боевой режим)
    run-status [--date]        — батч уровня 1: статусы сделок
    run-quality [--date]       — батч уровня 2: контроль качества исходящих
    digest [--date] [--send]   — собрать и отправить дайджест
    ingest file.json           — подложить событие вебхука из файла (проверка Э1/Э4)
    check "текст"              — прогнать текст через regex-слой стоп-паттернов
    rules                      — показать стоп-паттерны
    amo-check [--lead-id]      — проверить доступ к amo и записать тестовое примечание
    stats                      — сводка базы
    demo                       — весь конвейер на встроенных данных, без сети
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .amo import AmoError
from .analysis import run_status_batch
from .clock import parse_day
from .config import Settings, load_amo_tokens, load_settings
from .digest import build_digest
from .quality import run_quality_batch
from .redflags import describe_rules, scan_text
from .service import build_services

log = logging.getLogger("crmai")


def setup_logging(verbose: bool = False) -> None:
    """INFO — без текстов сообщений (раздел 4.5 ТЗ), DEBUG — с ними, локально."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crmai",
        description="ИИ-анализ диалогов Wazzup -> amoCRM. Сообщения партнёрам не отправляет.",
    )
    parser.add_argument("--env", default=None, help="путь к .env (по умолчанию ./.env)")
    parser.add_argument("--db", default=None, help="путь к SQLite-базе")
    parser.add_argument("--prompts", default=None, help="каталог промптов (по умолчанию prompts/)")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="вебхук + расписание батчей")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-scheduler", action="store_true", help="только вебхук, без батчей")

    status = sub.add_parser("run-status", help="батч уровня 1: статусы сделок")
    status.add_argument("--dry-run", action="store_true", help="не писать в amo")
    status.add_argument("--since", default=None, help="брать сообщения новее ISO-времени")

    quality = sub.add_parser("run-quality", help="батч уровня 2: контроль качества")
    quality.add_argument("--date", default=None, help="день в формате YYYY-MM-DD")
    quality.add_argument("--no-alerts", action="store_true", help="не слать алерты по критичным")

    digest = sub.add_parser("digest", help="собрать дайджест за день")
    digest.add_argument("--date", default=None, help="день в формате YYYY-MM-DD")
    digest.add_argument("--send", action="store_true", help="отправить в Telegram")

    ingest = sub.add_parser("ingest", help="обработать событие вебхука из JSON-файла")
    ingest.add_argument("file", help="файл с телом вебхука ('-' — stdin)")

    check = sub.add_parser("check", help="проверить текст regex-слоем стоп-паттернов")
    check.add_argument("text", nargs="+", help="текст сообщения")

    sub.add_parser("rules", help="показать стоп-паттерны")

    amo_check = sub.add_parser("amo-check", help="проверить доступ к amoCRM")
    amo_check.add_argument("--lead-id", type=int, default=None,
                           help="записать тестовое примечание в эту сделку")

    sub.add_parser("stats", help="сводка базы")
    sub.add_parser("demo", help="весь конвейер на встроенных данных, без сети")
    sub.add_parser("config-check", help="показать, чего не хватает в .env")

    return parser


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_amo_tokens(load_settings(args.env))
    if getattr(args, "db", None):
        settings.db_path = args.db
    if getattr(args, "prompts", None):
        settings.prompts_dir = args.prompts
    return settings


# --- команды ----------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .webhook import run as run_webhook

    settings = _settings(args)
    missing = settings.missing_required()
    if missing:
        log.warning("Не заданы переменные: %s — работаем в урезанном режиме", ", ".join(missing))

    services = build_services(settings)
    scheduler = None
    if not args.no_scheduler:
        from .scheduler import build_scheduler

        scheduler = build_scheduler(services)
        scheduler.start()

    try:
        run_webhook(settings=settings, services=services, host=args.host, port=args.port)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        services.close()
    return 0


def cmd_run_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with build_services(settings) as services:
        if services.llm is None:
            print("Не задан ANTHROPIC_API_KEY — анализировать нечем", file=sys.stderr)
            return 2
        result = run_status_batch(
            settings,
            services.db,
            services.llm,
            services.prompts,
            amo=None if args.dry_run else services.amo,
            since=args.since,
            dry_run=args.dry_run,
        )
        print(
            f"Чатов с активностью: {result.chats_seen} | проанализировано: {result.analyzed} | "
            f"примечаний: {result.notes} | задач: {result.tasks} | "
            f"без сделки: {result.unmatched} | ошибок: {result.failed}"
        )
        if result.budget_stopped:
            print("Батч остановлен: исчерпан дневной лимит вызовов LLM")
    return 0


def cmd_run_quality(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with build_services(settings) as services:
        if services.llm is None:
            print("Не задан ANTHROPIC_API_KEY — анализировать нечем", file=sys.stderr)
            return 2
        result = run_quality_batch(
            settings,
            services.db,
            services.llm,
            services.prompts,
            amo=services.amo,
            notifier=None if args.no_alerts else services.notifier,
            day=parse_day(args.date, settings),
        )
        print(
            f"Диалогов проверено: {result.chats_checked} | замечаний: {len(result.verdicts)} "
            f"(критичных {len(result.critical)}) | без замечаний: {result.ok_chats} | "
            f"ошибок: {result.failed}"
        )
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with build_services(settings, with_prompts=False) as services:
        text = build_digest(settings, services.db, parse_day(args.date, settings))
        print(text)
        if args.send:
            if services.notifier is None:
                print("Telegram не настроен — не отправлено", file=sys.stderr)
                return 2
            sent = services.notifier.broadcast(text)
            print(f"Отправлено получателям: {sent}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = _settings(args)
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        print(f"Не разобрали JSON: {exc}", file=sys.stderr)
        return 2
    with build_services(settings, with_prompts=False) as services:
        result = services.intake.handle_payload(payload)  # type: ignore[union-attr]
        print(json.dumps(result.as_dict(), ensure_ascii=False))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    flags = scan_text(text)
    if not flags:
        print("Стоп-паттернов не найдено")
        return 0
    for flag in flags:
        note = f" ({flag.note})" if flag.note else ""
        print(f"[{flag.rule}] {flag.title}{note}\n    «{flag.quote}»")
    return 0


def cmd_rules(_args: argparse.Namespace) -> int:
    print(describe_rules())
    return 0


def cmd_amo_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with build_services(settings, with_prompts=False) as services:
        if services.amo is None:
            print("amoCRM не настроен (нужны AMO_DOMAIN и AMO_ACCESS_TOKEN)", file=sys.stderr)
            return 2
        try:
            account = services.amo.whoami()
        except AmoError as exc:
            print(f"Доступ к amo не работает: {exc}", file=sys.stderr)
            return 1
        print(f"amo: подключение ок, аккаунт {account.get('name') or account.get('id')}")
        if args.lead_id:
            note_id = services.amo.add_note(
                args.lead_id, "🤖 Тестовое примечание анализатора — можно удалить"
            )
            print(f"Примечание записано в сделку {args.lead_id} (id {note_id})")
            print(settings.lead_url(args.lead_id))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with build_services(settings, with_prompts=False) as services:
        stats = services.db.stats()
    width = max(len(key) for key in stats)
    for key, value in stats.items():
        print(f"{key:<{width}} : {value}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    settings = _settings(args)
    run_demo(prompts_dir=settings.prompts_dir)
    return 0


def cmd_config_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    missing = settings.missing_required()
    print(f"Часовой пояс: {settings.tz} | батчи в {settings.batch_hour}:00 | модель {settings.llm_model}")
    print(f"База: {settings.db_path} | промпты: {settings.prompts_dir}")
    print(f"Воронки: {settings.pipeline_ids or 'не заданы'}")
    print(f"Дайджест менеджеру: {'да' if settings.send_to_manager else 'нет'}")
    if missing:
        print("Не заданы: " + ", ".join(missing))
        return 1
    print("Все обязательные переменные заданы")
    return 0


COMMANDS = {
    "serve": cmd_serve,
    "run-status": cmd_run_status,
    "run-quality": cmd_run_quality,
    "digest": cmd_digest,
    "ingest": cmd_ingest,
    "check": cmd_check,
    "rules": cmd_rules,
    "amo-check": cmd_amo_check,
    "stats": cmd_stats,
    "demo": cmd_demo,
    "config-check": cmd_config_check,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    handler = COMMANDS[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\nПрервано", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI не должен падать трейсбеком
        log.error("Команда %s завершилась с ошибкой: %s", args.command, exc)
        if args.verbose:
            raise
        return 1
