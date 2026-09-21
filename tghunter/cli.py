"""CLI парсера (раздел 11 ТЗ).

Команды:
    login                      — создать сессию Telethon (интерактивно, один раз)
    run --stream X | --all     — прогон пресета / всех активных (cron-режим)
    enrich file.csv            — обогатить метриками список каналов
    import-existing file.csv   — импорт текущей базы CRM для дедупа
    export --stream X          — CSV-выгрузка
    rescan                     — пересканировать метрики каналов старше 30 дней
    stats                      — сводка базы
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Settings, Stream, load_settings, load_streams
from .db import Database
from .discovery.manual import manual_candidates
from .exporters.csv_export import export_csv
from .exporters.notify import build_summary, send_summary
from .pipeline import Pipeline, export_dir_for_today
from .ratelimit import RateLimiter
from .search import build_adhoc_stream, format_table, run_search, split_queries

log = logging.getLogger("tghunter")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tghunter",
        description="Парсер Telegram-каналов для хантинга блогеров (read-only)",
    )
    parser.add_argument("--config-dir", default=None, help="каталог конфигов (по умолчанию config/)")
    parser.add_argument("--db", default=None, help="путь к SQLite-базе")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    parser.add_argument(
        "--demo", action="store_true",
        help="демо-режим: искать не в Telegram, а во встроенном корпусе каналов "
             "(без сессии и без Telethon)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="создать/проверить сессию Telethon")

    run = sub.add_parser("run", help="прогон пресета(ов)")
    group = run.add_mutually_exclusive_group(required=True)
    group.add_argument("--stream", help="имя пресета из streams.yaml")
    group.add_argument("--all", action="store_true", help="все активные пресеты (cron)")
    run.add_argument("--export", action="store_true", default=True,
                     help="выгрузить CSV после прогона (по умолчанию да)")
    run.add_argument("--no-export", dest="export", action="store_false")
    run.add_argument("--sheets", action="store_true", help="дублировать выгрузку в Google Sheets")
    run.add_argument("--amo", action="store_true", help="создать сделки в amoCRM")
    run.add_argument("--rescan", action="store_true",
                     help="после прогона обновить метрики каналов старше 30 дней")
    run.add_argument("--max-channels", type=int, default=None,
                     help="переопределить MAX_CHANNELS_PER_RUN")

    search = sub.add_parser(
        "search", help="свободный поиск по Telegram: ввели запрос — получили каналы"
    )
    search.add_argument("query", nargs="*",
                        help="запрос; несколько через запятую. Без аргумента — интерактивный режим")
    search.add_argument("--limit", type=int, default=30,
                        help="сколько каналов брать из выдачи на один запрос")
    search.add_argument("--expand", action="store_true",
                        help="расширить выдачу рекомендациями Telegram к найденным каналам")
    search.add_argument("--min-subs", type=int, default=1000, help="нижняя граница подписчиков")
    search.add_argument("--max-subs", type=int, default=1000000, help="верхняя граница подписчиков")
    search.add_argument("--lang", default=None,
                        help="языки через запятую (ru,uk,en,turkic). По умолчанию любые")
    search.add_argument("--loose", action="store_true",
                        help="не отсекать по ER и живости — показать всё, что нашлось")
    search.add_argument("--max-channels", type=int, default=None,
                        help="лимит новых каналов за поиск")
    search.add_argument("--top", type=int, default=40, help="сколько строк печатать в терминал")
    search.add_argument("--out", default=None, help="путь к CSV (по умолчанию в exports/)")
    search.add_argument("--no-export", dest="export", action="store_false", default=True,
                        help="не сохранять CSV")

    enrich = sub.add_parser("enrich", help="обогатить метриками список каналов")
    enrich.add_argument("file", help="CSV или txt со списком username/ссылок")
    enrich.add_argument("--stream", default="manual", help="под каким стримом сохранить")
    enrich.add_argument("--export", action="store_true", help="сразу выгрузить CSV")

    imp = sub.add_parser("import-existing", help="импорт базы CRM для дедупа")
    imp.add_argument("file", help="CSV со столбцом username или ссылкой")

    export = sub.add_parser("export", help="CSV-выгрузка из базы")
    export.add_argument("--stream", default=None, help="фильтр по стриму")
    export.add_argument("--new-only", action="store_true", help="только новые каналы")
    export.add_argument("--min-score", type=int, default=0, help="порог score")
    export.add_argument("--days", type=int, default=None,
                        help="только каналы, найденные за последние N дней")
    export.add_argument("--out", default=None, help="путь к CSV-файлу")
    export.add_argument("--sheets", action="store_true", help="выгрузить в Google Sheets")
    export.add_argument("--amo", action="store_true", help="создать сделки в amoCRM")
    export.add_argument("--mark-exported", action="store_true",
                        help="пометить выгруженные каналы статусом exported")

    rescan = sub.add_parser("rescan", help="пересканировать метрики старых каналов")
    rescan.add_argument("--older-than", type=int, default=30, help="старше скольки дней")
    rescan.add_argument("--limit", type=int, default=100, help="сколько каналов за раз")

    sub.add_parser("stats", help="сводка базы")

    return parser


# --- вспомогательное --------------------------------------------------------

def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings()
    if args.config_dir:
        settings.config_dir = args.config_dir
    if args.db:
        settings.db_path = args.db
    if getattr(args, "max_channels", None):
        settings.max_channels_per_run = args.max_channels
    if getattr(args, "demo", False):
        # в демо-режиме сети нет — ждать лимитов Telegram незачем
        settings.rate_min_interval = 0.0
        settings.rate_max_interval = 0.0
        settings.method_pause_min = 0.0
        settings.method_pause_max = 0.0
    return settings


def _streams(settings: Settings) -> dict[str, Stream]:
    return load_streams(Path(settings.config_dir) / "streams.yaml")


def _limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(
        min_interval=settings.rate_min_interval,
        max_interval=settings.rate_max_interval,
        floodwait_abort_streak=settings.floodwait_abort_streak,
    )


def _gateway(settings: Settings, limiter: RateLimiter, demo: bool = False):
    if demo:
        from .demo import DemoGateway, corpus_size

        log.info("Демо-режим: встроенный корпус из %d каналов, сеть не используется",
                 corpus_size())
        return DemoGateway()

    from .tg import TelegramGateway

    gateway = TelegramGateway(settings, limiter)
    gateway.connect()
    return gateway


def _do_optional_exports(
    settings: Settings, rows: list[Any], args: argparse.Namespace, worksheet: str
) -> None:
    if getattr(args, "sheets", False):
        from .exporters.sheets import export_to_sheets

        result = export_to_sheets(settings, rows, worksheet)
        print(f"Google Sheets: записано {result['written']} строк на лист {result['worksheet']}")

    if getattr(args, "amo", False):
        from .exporters.amocrm import export_to_amo

        result = export_to_amo(settings, rows)
        print(
            f"amoCRM: создано {result['created']}, пропущено дублей {result['skipped']}"
            f" из {result['total']}"
        )


# --- команды ----------------------------------------------------------------

def cmd_login(args: argparse.Namespace) -> int:
    """Интерактивное создание сессии Telethon — выполняется один раз при установке."""
    settings = _settings(args)
    from .tg import TelegramGateway, TelegramUnavailable

    try:
        gateway = TelegramGateway(settings, _limiter(settings))
    except TelegramUnavailable as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    client = gateway.client
    print(f"Создаём сессию: {settings.session_path}.session")
    print("ВАЖНО: используйте ОТДЕЛЬНЫЙ юзер-аккаунт, не личный и не основной рабочий.")
    client.start()  # Telethon сам спросит телефон, код и 2FA-пароль
    me = client.loop.run_until_complete(client.get_me())
    print(f"Авторизован: {getattr(me, 'username', None) or getattr(me, 'first_name', '')}")
    gateway.disconnect()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    streams = _streams(settings)

    if args.all:
        selected = [s for s in streams.values() if s.enabled]
        if not selected:
            print("Нет активных пресетов в streams.yaml", file=sys.stderr)
            return 1
    else:
        if args.stream not in streams:
            print(
                f"Пресет '{args.stream}' не найден. Доступны: {', '.join(sorted(streams))}",
                file=sys.stderr,
            )
            return 1
        selected = [streams[args.stream]]

    limiter = _limiter(settings)
    db = Database(settings.db_path)

    try:
        gateway = _gateway(settings, limiter, demo=getattr(args, "demo", False))
    except Exception as exc:
        print(f"Ошибка подключения к Telegram: {exc}", file=sys.stderr)
        db.close()
        return 1

    pipeline = Pipeline(gateway, db, settings, limiter, settings.config_dir)
    results: list[dict[str, Any]] = []
    aborted = False

    try:
        for stream in selected:
            log.info("=== Стрим %s ===", stream.name)
            result = pipeline.run_stream(stream)
            results.append(result)
            print(
                f"[{result['stream']}] проверено {result['checked']}, "
                f"новых {result['new']}, статус {result['status']}"
            )
            if result["aborted"]:
                aborted = True
                log.error("Останавливаем остальные стримы: прогон упёрся в лимиты Telegram")
                break

        if args.rescan and not aborted:
            rescan = pipeline.rescan_stale(streams)
            results.append(rescan)
            print(f"[rescan] обновлено {rescan['updated']} из {rescan['checked']}")
            aborted = aborted or rescan["aborted"]
    finally:
        gateway.disconnect()

    exported_rows: list[Any] = []
    if args.export:
        out_dir = export_dir_for_today(settings.export_dir)
        for stream in selected:
            rows = db.export_rows(stream=stream.name, new_only=True)
            if not rows:
                continue
            result = export_csv(rows, out_dir / f"{stream.name}.csv")
            exported_rows.extend(rows)
            print(
                f"CSV [{stream.name}]: {result['main_count']} с контактом -> "
                f"{result['main_file']}"
                + (
                    f", {result['no_contact_count']} без контакта -> {result['no_contact_file']}"
                    if result["no_contact_file"]
                    else ""
                )
            )
            db.mark_exported([r["channel_id"] for r in rows])

        if exported_rows:
            _do_optional_exports(
                settings, exported_rows, args, f"{settings.sheets_worksheet_prefix}-new"
            )

    if settings.summary_enabled:
        top = sorted(exported_rows, key=lambda r: r["score"], reverse=True)[:5]
        send_summary(
            settings.bot_token,
            settings.summary_chat_id,
            build_summary(results, top, aborted=aborted),
        )

    db.close()
    return 2 if aborted else 0


def cmd_search(args: argparse.Namespace) -> int:
    """Свободный поиск: запрос из аргумента или интерактивно."""
    settings = _settings(args)
    queries = split_queries(" ".join(args.query)) if args.query else []

    if not queries:
        try:
            typed = input("Запрос (несколько через запятую): ").strip()
        except EOFError:
            typed = ""
        queries = split_queries(typed)
    if not queries:
        print("Пустой запрос — нечего искать", file=sys.stderr)
        return 1

    languages = [l.strip() for l in args.lang.split(",") if l.strip()] if args.lang else []
    stream = build_adhoc_stream(
        queries,
        min_subs=args.min_subs,
        max_subs=args.max_subs,
        languages=languages,
        loose=args.loose,
    )

    limiter = _limiter(settings)
    db = Database(settings.db_path)
    try:
        gateway = _gateway(settings, limiter, demo=getattr(args, "demo", False))
    except Exception as exc:
        print(f"Ошибка подключения к Telegram: {exc}", file=sys.stderr)
        db.close()
        return 1

    try:
        result = run_search(
            gateway, db, settings, limiter, queries, stream,
            limit_per_query=args.limit,
            expand=args.expand,
            max_channels=args.max_channels,
            config_dir=settings.config_dir,
        )
    finally:
        gateway.disconnect()

    print(f"\nЗапрос: {result['query']}")
    print(
        f"Найдено {result['found']} каналов "
        f"(новых {result['new']}, уже в базе {result['known']})\n"
    )
    print(format_table(result["rows"], limit=args.top))

    if result["aborted"]:
        print(f"\nПрогон остановлен по лимитам Telegram: {result['note']}", file=sys.stderr)

    if args.export and result["rows"]:
        exportable = [r for r in result["rows"] if r["passed_filters"] and not r["blacklist_hit"]]
        if exportable:
            slug = "".join(c if c.isalnum() else "_" for c in result["query"])[:40] or "search"
            out = Path(args.out) if args.out else (
                export_dir_for_today(settings.export_dir) / f"search_{slug}.csv"
            )
            csv_result = export_csv(exportable, out)
            print(f"\nCSV: {csv_result['main_count']} строк -> {csv_result['main_file']}")
            if csv_result["no_contact_file"]:
                print(
                    f"CSV: {csv_result['no_contact_count']} без контакта -> "
                    f"{csv_result['no_contact_file']}"
                )
        else:
            print("\nВ выгрузку никто не прошёл — попробуйте --loose или другие пороги.")

    db.close()
    return 2 if result["aborted"] else 0


def cmd_enrich(args: argparse.Namespace) -> int:
    settings = _settings(args)
    streams = _streams(settings)
    stream = streams.get(args.stream) or Stream(name=args.stream)

    candidates = manual_candidates(args.file)
    if not candidates:
        print(f"В файле {args.file} не найдено каналов", file=sys.stderr)
        return 1

    from .config import load_blacklist

    limiter = _limiter(settings)
    db = Database(settings.db_path)
    try:
        gateway = _gateway(settings, limiter, demo=getattr(args, "demo", False))
    except Exception as exc:
        print(f"Ошибка подключения к Telegram: {exc}", file=sys.stderr)
        db.close()
        return 1

    pipeline = Pipeline(gateway, db, settings, limiter, settings.config_dir)
    blacklist = load_blacklist(settings.config_dir, stream.extra_blacklist)
    run_id = db.start_run(f"enrich:{stream.name}")

    processed = new_count = 0
    from .ratelimit import RunAborted

    try:
        for candidate in candidates:
            result = pipeline.process_candidate(candidate, stream, blacklist, run_id)
            processed += 1
            if result and result[1]:
                new_count += 1
    except RunAborted as exc:
        print(f"Остановлен по лимитам: {exc}", file=sys.stderr)
        db.finish_run(run_id, "aborted_by_limits", processed, new_count, str(exc))
        gateway.disconnect()
        db.close()
        return 2
    finally:
        gateway.disconnect()

    db.finish_run(run_id, "ok", processed, new_count)
    print(f"Обогащено {processed} каналов, новых в базе: {new_count}")

    if args.export:
        rows = db.export_rows(stream=stream.name)
        out = export_dir_for_today(settings.export_dir) / f"{stream.name}_enriched.csv"
        result = export_csv(rows, out)
        print(f"CSV: {result['main_count']} строк -> {result['main_file']}")

    db.close()
    return 0


def cmd_import_existing(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .discovery.manual import read_channel_list

    try:
        usernames = read_channel_list(args.file)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    db = Database(settings.db_path)
    added, skipped = db.import_existing(usernames)
    db.close()
    print(
        f"Импортировано {added} каналов из {len(usernames)}, "
        f"пропущено (уже в базе / нечитаемые) {skipped}"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db = Database(settings.db_path)

    since: Optional[str] = None
    if args.days:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    rows = db.export_rows(
        stream=args.stream,
        new_only=args.new_only,
        min_score=args.min_score,
        since=since,
    )
    if not rows:
        print("Нечего выгружать: под фильтры не попал ни один канал")
        db.close()
        return 0

    if args.out:
        out_path = Path(args.out)
    else:
        name = args.stream or "all"
        suffix = "_new" if args.new_only else ""
        out_path = export_dir_for_today(settings.export_dir) / f"{name}{suffix}.csv"

    result = export_csv(rows, out_path)
    print(f"CSV: {result['main_count']} с контактом -> {result['main_file']}")
    if result["no_contact_file"]:
        print(
            f"CSV: {result['no_contact_count']} без контакта -> {result['no_contact_file']}"
        )

    _do_optional_exports(
        settings, rows, args, f"{settings.sheets_worksheet_prefix}-{args.stream or 'all'}"
    )

    if args.mark_exported:
        db.mark_exported([r["channel_id"] for r in rows])
        print(f"Помечено как exported: {len(rows)}")

    db.close()
    return 0


def cmd_rescan(args: argparse.Namespace) -> int:
    settings = _settings(args)
    streams = _streams(settings)
    limiter = _limiter(settings)
    db = Database(settings.db_path)

    try:
        gateway = _gateway(settings, limiter, demo=getattr(args, "demo", False))
    except Exception as exc:
        print(f"Ошибка подключения к Telegram: {exc}", file=sys.stderr)
        db.close()
        return 1

    pipeline = Pipeline(gateway, db, settings, limiter, settings.config_dir)
    try:
        result = pipeline.rescan_stale(streams, args.older_than, args.limit)
    finally:
        gateway.disconnect()

    print(f"Пересканировано {result['updated']} из {result['checked']} каналов")
    db.close()
    return 2 if result["aborted"] else 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = _settings(args)
    db = Database(settings.db_path)
    stats = db.stats()
    db.close()

    print(f"Всего каналов в базе: {stats['total']}")
    print(f"Новых за неделю:      {stats['new_last_week']}")
    print(f"Готовы к выгрузке:    {stats['exportable']}")
    print(f"В блэклисте:          {stats['blacklisted']}")
    print(f"Без контакта:         {stats['no_contact']}")

    if stats["by_stream"]:
        print("\nПо стримам:")
        for stream, count in stats["by_stream"].items():
            print(f"  {stream or '(без стрима)':<16} {count}")

    if stats["by_status"]:
        print("\nПо статусам:")
        for status, count in stats["by_status"].items():
            print(f"  {status:<16} {count}")

    if stats["recent_runs"]:
        print("\nПоследние прогоны:")
        for run in stats["recent_runs"]:
            print(
                f"  {run['started_at'][:19]} {run['stream']:<14} "
                f"{run['status']:<18} новых {run['new_channels']}"
                + (f" — {run['note']}" if run["note"] else "")
            )
    return 0


_COMMANDS = {
    "login": cmd_login,
    "run": cmd_run,
    "search": cmd_search,
    "enrich": cmd_enrich,
    "import-existing": cmd_import_existing,
    "export": cmd_export,
    "rescan": cmd_rescan,
    "stats": cmd_stats,
}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    handler = _COMMANDS[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nПрервано пользователем", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
