"""Короткая сводка прогона в Telegram-бота (раздел 8.2 ТЗ, опционально)."""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def build_summary(results: list[dict[str, Any]], top: list[Any], aborted: bool = False) -> str:
    """Текст сводки: сколько найдено и топ-5 по score."""
    total_new = sum(r.get("new", 0) for r in results)
    total_checked = sum(r.get("checked", 0) for r in results)

    lines = [
        "<b>Хантинг каналов — итог прогона</b>",
        f"Найдено новых: <b>{total_new}</b> (проверено {total_checked})",
    ]

    for result in results:
        lines.append(
            f"• {result.get('stream')}: новых {result.get('new', 0)}, "
            f"проверено {result.get('checked', 0)}"
            + (f" — {result['note']}" if result.get("note") else "")
        )

    if top:
        lines.append("")
        lines.append("<b>Топ-5 по score:</b>")
        for row in top[:5]:
            username = row["username"] or row["channel_id"]
            lines.append(
                f"{row['score']} — @{username} "
                f"({row['subscribers']} подп., ER {row['er']}%)"
            )

    if aborted:
        lines.append("")
        lines.append("⚠️ Прогон <b>остановлен по лимитам</b> Telegram.")

    return "\n".join(lines)


def send_summary(
    bot_token: str, chat_id: str, text: str, session: Optional[Any] = None
) -> bool:
    """Отправляет сводку через Bot API. Ошибка отправки не роняет прогон."""
    if not bot_token or not chat_id:
        log.info("Сводка в Telegram отключена (нет TG_BOT_TOKEN/TG_SUMMARY_CHAT_ID)")
        return False

    try:
        import requests
    except ImportError:  # pragma: no cover
        log.warning("Нет requests — сводка не отправлена")
        return False

    http = session or requests
    try:
        response = http.post(
            f"{TELEGRAM_API}/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        ok = getattr(response, "status_code", 0) == 200
        if not ok:
            log.warning("Сводка не отправлена: HTTP %s", getattr(response, "status_code", "?"))
        return ok
    except Exception as exc:
        log.warning("Сводка не отправлена: %s", exc)
        return False
