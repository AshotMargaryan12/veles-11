"""SQLite-хранилище анализатора (раздел 4.1 ТЗ).

Таблицы:
  messages  — каждое событие Wazzup, дедуп по message_id;
  chats     — чат, контакт и кэш матчинга chat_id -> lead_id;
  analyses  — сохранённые статусы сделки (уровень 1), дают prev_summary;
  verdicts  — замечания контроля качества (уровень 2) за день;
  alerts    — отправленные алерты, чтобы не дублировать при ретраях вебхука;
  llm_usage — счётчик вызовов LLM за сутки (MAX_LLM_CALLS_PER_DAY);
  failures  — чаты, которые не удалось обработать: идут строкой в дайджест.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import DIRECTION_OUT, Message

SCHEMA_VERSION = 1

# Через сколько часов кэш матчинга chat_id -> lead_id считается протухшим
MATCH_TTL_HOURS = 24

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id   TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL,
    chat_type    TEXT DEFAULT '',
    direction    TEXT NOT NULL,
    text         TEXT DEFAULT '',
    ts           TEXT NOT NULL,
    contact_name TEXT DEFAULT '',
    is_media     INTEGER DEFAULT 0,
    raw_json     TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_ts   ON messages(ts);

CREATE TABLE IF NOT EXISTS chats (
    chat_id          TEXT PRIMARY KEY,
    chat_type        TEXT DEFAULT '',
    contact_name     TEXT DEFAULT '',
    contact_username TEXT DEFAULT '',
    contact_phone    TEXT DEFAULT '',
    amo_contact_id   INTEGER,
    amo_lead_id      INTEGER,
    matched_at       TEXT,
    unmatched        INTEGER DEFAULT 0,
    last_message_ts  TEXT,
    last_analyzed_ts TEXT,
    first_seen       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_lead ON chats(amo_lead_id);

CREATE TABLE IF NOT EXISTS analyses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL,
    lead_id      INTEGER,
    summary_json TEXT NOT NULL,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_chat ON analyses(chat_id, ts);

CREATE TABLE IF NOT EXISTS verdicts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    day          TEXT NOT NULL,
    chat_id      TEXT NOT NULL,
    lead_id      INTEGER,
    severity     TEXT NOT NULL,
    partner      TEXT DEFAULT '',
    quote        TEXT DEFAULT '',
    issue        TEXT DEFAULT '',
    suggestion   TEXT DEFAULT '',
    alerted      INTEGER DEFAULT 0,
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verdicts_day ON verdicts(day);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    chat_id    TEXT,
    rule       TEXT NOT NULL,
    quote      TEXT DEFAULT '',
    sent       INTEGER DEFAULT 0,
    ts         TEXT NOT NULL,
    UNIQUE(message_id, rule)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day   TEXT PRIMARY KEY,
    calls INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS failures (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    day     TEXT NOT NULL,
    kind    TEXT NOT NULL,
    chat_id TEXT,
    error   TEXT DEFAULT '',
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures_day ON failures(day);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Обёртка над sqlite3 с операциями анализатора."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(_SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # --- сообщения --------------------------------------------------------

    def add_message(self, message: Message) -> bool:
        """Сохраняет сообщение. False — дубль по message_id (Wazzup ретраит события)."""
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO messages
                       (message_id, chat_id, chat_type, direction, text, ts,
                        contact_name, is_media, raw_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        message.message_id,
                        message.chat_id,
                        message.chat_type,
                        message.direction,
                        message.text,
                        message.ts,
                        message.contact_name,
                        int(message.is_media),
                        json.dumps(message.raw, ensure_ascii=False, default=str),
                        utcnow(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        self._touch_chat(message)
        return True

    def _touch_chat(self, message: Message) -> None:
        """Создаёт чат при первом сообщении, обновляет контакт и время активности."""
        now = utcnow()
        with self.conn:
            self.conn.execute(
                """INSERT INTO chats (chat_id, chat_type, contact_name, contact_username,
                                      contact_phone, amo_contact_id, amo_lead_id,
                                      last_message_ts, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       chat_type        = COALESCE(NULLIF(excluded.chat_type, ''), chats.chat_type),
                       contact_name     = COALESCE(NULLIF(excluded.contact_name, ''), chats.contact_name),
                       contact_username = COALESCE(NULLIF(excluded.contact_username, ''), chats.contact_username),
                       contact_phone    = COALESCE(NULLIF(excluded.contact_phone, ''), chats.contact_phone),
                       amo_contact_id   = COALESCE(excluded.amo_contact_id, chats.amo_contact_id),
                       amo_lead_id      = COALESCE(excluded.amo_lead_id, chats.amo_lead_id),
                       last_message_ts  = MAX(COALESCE(chats.last_message_ts, ''), excluded.last_message_ts)""",
                (
                    message.chat_id,
                    message.chat_type,
                    message.contact_name,
                    message.contact_username,
                    message.contact_phone,
                    message.amo_contact_id,
                    message.amo_lead_id,
                    message.ts,
                    now,
                ),
            )

    def messages_for_chat(
        self, chat_id: str, since: Optional[str] = None, limit: int = 60
    ) -> list[sqlite3.Row]:
        """Последние `limit` сообщений чата в хронологическом порядке."""
        sql = "SELECT * FROM messages WHERE chat_id = ?"
        params: list[Any] = [chat_id]
        if since:
            sql += " AND ts > ?"
            params.append(since)
        sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return list(reversed(rows))

    def outgoing_for_day(self, chat_id: str, day_start: str, day_end: str) -> list[sqlite3.Row]:
        """Исходящие менеджера за сутки — вход контроля качества."""
        return list(
            self.conn.execute(
                """SELECT * FROM messages
                   WHERE chat_id = ? AND direction = ? AND ts >= ? AND ts < ?
                   ORDER BY ts ASC, rowid ASC""",
                (chat_id, DIRECTION_OUT, day_start, day_end),
            ).fetchall()
        )

    def messages_before(self, chat_id: str, ts: str, limit: int) -> list[sqlite3.Row]:
        """N сообщений, предшествующих моменту ts (контекст для промпта П2)."""
        rows = self.conn.execute(
            """SELECT * FROM messages WHERE chat_id = ? AND ts < ?
               ORDER BY ts DESC, rowid DESC LIMIT ?""",
            (chat_id, ts, limit),
        ).fetchall()
        return list(reversed(rows))

    # --- чаты -------------------------------------------------------------

    def get_chat(self, chat_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()

    def chats_with_activity(self, since: Optional[str] = None) -> list[sqlite3.Row]:
        """Чаты, где после last_analyzed_ts появились новые сообщения (раздел 4.3 ТЗ)."""
        sql = """SELECT * FROM chats
                 WHERE last_message_ts IS NOT NULL
                   AND (last_analyzed_ts IS NULL OR last_message_ts > last_analyzed_ts)"""
        params: list[Any] = []
        if since:
            sql += " AND last_message_ts >= ?"
            params.append(since)
        sql += " ORDER BY last_message_ts ASC"
        return list(self.conn.execute(sql, params).fetchall())

    def chats_active_between(self, day_start: str, day_end: str) -> list[sqlite3.Row]:
        """Чаты с любой активностью за период — база для дайджеста и уровня 2."""
        return list(
            self.conn.execute(
                """SELECT c.* FROM chats c
                   WHERE EXISTS (SELECT 1 FROM messages m
                                 WHERE m.chat_id = c.chat_id AND m.ts >= ? AND m.ts < ?)
                   ORDER BY c.last_message_ts ASC""",
                (day_start, day_end),
            ).fetchall()
        )

    def set_match(
        self,
        chat_id: str,
        lead_id: Optional[int],
        contact_id: Optional[int] = None,
        unmatched: bool = False,
    ) -> None:
        """Кэширует результат матчинга чат -> сделка."""
        with self.conn:
            self.conn.execute(
                """UPDATE chats SET amo_lead_id = ?, amo_contact_id = COALESCE(?, amo_contact_id),
                       matched_at = ?, unmatched = ? WHERE chat_id = ?""",
                (lead_id, contact_id, utcnow(), int(unmatched), chat_id),
            )

    def match_is_fresh(self, chat: sqlite3.Row, ttl_hours: int = MATCH_TTL_HOURS) -> bool:
        """Кэш матчинга живёт сутки (раздел 3.2 ТЗ), потом ищем сделку заново."""
        if not chat["matched_at"] or not chat["amo_lead_id"]:
            return False
        try:
            matched = datetime.fromisoformat(chat["matched_at"])
        except (TypeError, ValueError):
            return False
        if matched.tzinfo is None:
            matched = matched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - matched < timedelta(hours=ttl_hours)

    def mark_analyzed(self, chat_id: str, ts: Optional[str] = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE chats SET last_analyzed_ts = ? WHERE chat_id = ?",
                (ts or utcnow(), chat_id),
            )

    def unmatched_count(self, day_start: str, day_end: str) -> int:
        """Сколько диалогов с активностью за период остались без сделки в amo."""
        row = self.conn.execute(
            """SELECT COUNT(*) c FROM chats c
               WHERE c.amo_lead_id IS NULL
                 AND EXISTS (SELECT 1 FROM messages m
                             WHERE m.chat_id = c.chat_id AND m.ts >= ? AND m.ts < ?)""",
            (day_start, day_end),
        ).fetchone()
        return int(row["c"])

    # --- анализы ----------------------------------------------------------

    def save_analysis(self, chat_id: str, lead_id: Optional[int], summary: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO analyses (chat_id, lead_id, summary_json, ts) VALUES (?, ?, ?, ?)",
                (chat_id, lead_id, json.dumps(summary, ensure_ascii=False), utcnow()),
            )

    def last_analysis(self, chat_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT summary_json FROM analyses WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["summary_json"])
        except ValueError:
            return None

    def analyses_since(self, since: str) -> list[dict[str, Any]]:
        """Свежие статусы: из них собираются блоки «горячие» и «зависшие» в дайджесте."""
        rows = self.conn.execute(
            """SELECT a.chat_id, a.lead_id, a.summary_json, a.ts
                 FROM analyses a
                 JOIN (SELECT chat_id, MAX(id) mid FROM analyses WHERE ts >= ? GROUP BY chat_id) last
                   ON a.id = last.mid
                ORDER BY a.ts ASC""",
            (since,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                summary = json.loads(row["summary_json"])
            except ValueError:
                continue
            result.append({"chat_id": row["chat_id"], "lead_id": row["lead_id"], "summary": summary})
        return result

    # --- контроль качества -------------------------------------------------

    def save_verdicts(self, day: str, verdicts: Iterable[Any]) -> int:
        """Кладёт замечания дня. Возвращает, сколько записано."""
        rows = [
            (
                day,
                v.chat_id,
                v.lead_id,
                v.severity,
                v.chat,
                v.quote,
                v.issue,
                v.suggestion,
                utcnow(),
            )
            for v in verdicts
        ]
        if not rows:
            return 0
        with self.conn:
            self.conn.executemany(
                """INSERT INTO verdicts (day, chat_id, lead_id, severity, partner,
                                         quote, issue, suggestion, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def clear_verdicts(self, day: str) -> int:
        """Убирает замечания дня: повторный прогон батча не должен их удваивать."""
        with self.conn:
            cursor = self.conn.execute("DELETE FROM verdicts WHERE day = ?", (day,))
        return cursor.rowcount

    def verdicts_for_day(self, day: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT * FROM verdicts WHERE day = ?
                   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'major' THEN 1
                                          ELSE 2 END, id""",
                (day,),
            ).fetchall()
        )

    # --- алерты -----------------------------------------------------------

    def register_alert(self, message_id: str, chat_id: str, rule: str, quote: str) -> bool:
        """True — алерт по этой паре (сообщение, правило) ещё не отправляли."""
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO alerts (message_id, chat_id, rule, quote, sent, ts)
                       VALUES (?, ?, ?, ?, 0, ?)""",
                    (message_id, chat_id, rule, quote[:500], utcnow()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def mark_alert_sent(self, message_id: str, rule: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE alerts SET sent = 1 WHERE message_id = ? AND rule = ?",
                (message_id, rule),
            )

    def alerts_for_day(self, day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM alerts WHERE ts LIKE ?", (f"{day}%",)
        ).fetchone()
        return int(row["c"])

    # --- бюджет LLM -------------------------------------------------------

    def llm_calls_today(self, day: str) -> int:
        row = self.conn.execute("SELECT calls FROM llm_usage WHERE day = ?", (day,)).fetchone()
        return int(row["calls"]) if row else 0

    def bump_llm_calls(self, day: str, count: int = 1) -> int:
        with self.conn:
            self.conn.execute(
                """INSERT INTO llm_usage (day, calls) VALUES (?, ?)
                   ON CONFLICT(day) DO UPDATE SET calls = calls + excluded.calls""",
                (day, count),
            )
        return self.llm_calls_today(day)

    # --- сбои -------------------------------------------------------------

    def record_failure(self, day: str, kind: str, chat_id: Optional[str], error: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO failures (day, kind, chat_id, error, ts) VALUES (?, ?, ?, ?, ?)",
                (day, kind, chat_id, str(error)[:500], utcnow()),
            )

    def failures_for_day(self, day: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM failures WHERE day = ? ORDER BY id", (day,)).fetchall()
        )

    # --- meta -------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO meta (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, str(value)),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    # --- сводка -----------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        cur = self.conn
        return {
            "messages": cur.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
            "chats": cur.execute("SELECT COUNT(*) c FROM chats").fetchone()["c"],
            "matched": cur.execute(
                "SELECT COUNT(*) c FROM chats WHERE amo_lead_id IS NOT NULL"
            ).fetchone()["c"],
            "unmatched": cur.execute(
                "SELECT COUNT(*) c FROM chats WHERE amo_lead_id IS NULL"
            ).fetchone()["c"],
            "analyses": cur.execute("SELECT COUNT(*) c FROM analyses").fetchone()["c"],
            "verdicts": cur.execute("SELECT COUNT(*) c FROM verdicts").fetchone()["c"],
            "alerts": cur.execute("SELECT COUNT(*) c FROM alerts").fetchone()["c"],
            "llm_calls_today": self.llm_calls_today(datetime.now().strftime("%Y-%m-%d")),
        }
