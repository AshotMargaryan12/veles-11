"""SQLite-хранилище каналов и история обходов (раздел 7 ТЗ).

Дедуп двухуровневый: по channel_id и по username (регистронезависимо).
Повторная находка обновляет метрики, а не создаёт дубль.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    STATUS_BLACKLIST,
    STATUS_EXPORTED,
    STATUS_IMPORTED,
    STATUS_NEW,
    STATUS_NO_CONTACT,
)

SCHEMA_VERSION = 1

# Колонки метрик, которые обновляются при каждой перепроверке канала
_METRIC_COLUMNS = (
    "title",
    "about",
    "link",
    "subscribers",
    "er",
    "er_median",
    "avg_views",
    "median_views",
    "avg_reactions",
    "comments_enabled",
    "days_since_last_post",
    "posts_last_30d",
    "last_post_date",
    "language",
    "monetized",
    "monetization_markers",
    "contact",
    "blacklist_hit",
    "blacklist_markers",
    "score",
    "score_breakdown",
    "passed_filters",
    "filter_reasons",
    "metrics_json",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id           INTEGER PRIMARY KEY,
    username             TEXT COLLATE NOCASE,
    title                TEXT DEFAULT '',
    about                TEXT DEFAULT '',
    link                 TEXT DEFAULT '',
    subscribers          INTEGER DEFAULT 0,
    er                   REAL DEFAULT 0,
    er_median            REAL DEFAULT 0,
    avg_views            REAL DEFAULT 0,
    median_views         INTEGER DEFAULT 0,
    avg_reactions        REAL DEFAULT 0,
    comments_enabled     INTEGER DEFAULT 0,
    days_since_last_post INTEGER,
    posts_last_30d       INTEGER DEFAULT 0,
    last_post_date       TEXT,
    language             TEXT DEFAULT '',
    monetized            INTEGER DEFAULT 0,
    monetization_markers TEXT DEFAULT '',
    contact              TEXT,
    blacklist_hit        INTEGER DEFAULT 0,
    blacklist_markers    TEXT DEFAULT '',
    score                INTEGER DEFAULT 0,
    score_breakdown      TEXT DEFAULT '',
    stream               TEXT DEFAULT '',
    method               TEXT DEFAULT '',
    source_channel       TEXT,
    status               TEXT DEFAULT 'new',
    passed_filters       INTEGER DEFAULT 0,
    filter_reasons       TEXT DEFAULT '',
    metrics_json         TEXT DEFAULT '{}',
    first_seen           TEXT NOT NULL,
    last_checked         TEXT NOT NULL,
    exported_at          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_username
    ON channels(username) WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_channels_stream  ON channels(stream);
CREATE INDEX IF NOT EXISTS idx_channels_status  ON channels(status);
CREATE INDEX IF NOT EXISTS idx_channels_score   ON channels(score);
CREATE INDEX IF NOT EXISTS idx_channels_checked ON channels(last_checked);

-- История обходов
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stream       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT DEFAULT 'running',
    checked      INTEGER DEFAULT 0,
    new_channels INTEGER DEFAULT 0,
    note         TEXT DEFAULT ''
);

-- Лог находок: какой метод и от какого канала привёл к кандидату
CREATE TABLE IF NOT EXISTS crawl_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER,
    channel_key    TEXT NOT NULL,
    method         TEXT NOT NULL,
    source_channel TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_run ON crawl_log(run_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Тонкая обёртка над sqlite3 с операциями хантинга."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
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

    # --- дедуп ------------------------------------------------------------

    def find(
        self, channel_id: Optional[int] = None, username: Optional[str] = None
    ) -> Optional[sqlite3.Row]:
        """Ищет канал по id, затем по username (регистронезависимо)."""
        if channel_id:
            row = self.conn.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
            if row:
                return row
        if username:
            row = self.conn.execute(
                "SELECT * FROM channels WHERE username = ? COLLATE NOCASE",
                (username.lstrip("@"),),
            ).fetchone()
            if row:
                return row
        return None

    def exists(
        self, channel_id: Optional[int] = None, username: Optional[str] = None
    ) -> bool:
        return self.find(channel_id, username) is not None

    def known_keys(self) -> set[str]:
        """Все известные username (в нижнем регистре) и id — для быстрого дедупа."""
        keys: set[str] = set()
        for row in self.conn.execute("SELECT channel_id, username FROM channels"):
            if row["username"]:
                keys.add(row["username"].lower())
            if row["channel_id"]:
                keys.add(f"id:{row['channel_id']}")
        return keys

    # --- запись -----------------------------------------------------------

    def upsert_channel(
        self,
        metrics: dict[str, Any],
        score: int,
        breakdown: str,
        stream: str,
        passed: bool,
        reasons: list[str],
    ) -> tuple[int, bool]:
        """Создаёт или обновляет канал. Возвращает (channel_id, is_new)."""
        channel_id = int(metrics["channel_id"])
        username = (metrics.get("username") or "").lstrip("@") or None
        existing = self.find(channel_id, username)
        now = _now()

        status = _resolve_status(metrics, passed, existing)

        values = {
            "title": metrics.get("title") or "",
            "about": metrics.get("about") or "",
            "link": metrics.get("link") or "",
            "subscribers": int(metrics.get("subscribers") or 0),
            "er": float(metrics.get("er") or 0),
            "er_median": float(metrics.get("er_median") or 0),
            "avg_views": float(metrics.get("avg_views") or 0),
            "median_views": int(metrics.get("median_views") or 0),
            "avg_reactions": float(metrics.get("avg_reactions") or 0),
            "comments_enabled": int(bool(metrics.get("comments_enabled"))),
            "days_since_last_post": metrics.get("days_since_last_post"),
            "posts_last_30d": int(metrics.get("posts_last_30d") or 0),
            "last_post_date": metrics.get("last_post_date"),
            "language": metrics.get("language") or "",
            "monetized": int(bool(metrics.get("monetized"))),
            "monetization_markers": ",".join(metrics.get("monetization_markers") or []),
            "contact": metrics.get("contact"),
            "blacklist_hit": int(bool(metrics.get("blacklist_hit"))),
            "blacklist_markers": ",".join(metrics.get("blacklist_markers") or []),
            "score": int(score),
            "score_breakdown": breakdown,
            "passed_filters": int(bool(passed)),
            "filter_reasons": "; ".join(reasons),
            "metrics_json": json.dumps(metrics, ensure_ascii=False, default=str),
        }

        if existing:
            assignments = ", ".join(f"{col} = :{col}" for col in _METRIC_COLUMNS)
            params = dict(values)
            params.update(
                {
                    "last_checked": now,
                    "status": status,
                    "row_id": existing["channel_id"],
                    "username": username or existing["username"],
                }
            )
            self.conn.execute(
                f"""UPDATE channels SET {assignments},
                    username = :username, last_checked = :last_checked, status = :status
                    WHERE channel_id = :row_id""",
                params,
            )
            self.conn.commit()
            return int(existing["channel_id"]), False

        params = dict(values)
        params.update(
            {
                "channel_id": channel_id,
                "username": username,
                "stream": stream,
                "method": metrics.get("method") or "",
                "source_channel": metrics.get("source_channel"),
                "status": status,
                "first_seen": now,
                "last_checked": now,
            }
        )
        columns = list(params)
        placeholders = ", ".join(f":{c}" for c in columns)
        self.conn.execute(
            f"INSERT INTO channels ({', '.join(columns)}) VALUES ({placeholders})",
            params,
        )
        self.conn.commit()
        return channel_id, True

    def import_existing(self, usernames: Iterable[str]) -> tuple[int, int]:
        """Импорт текущей базы CRM для дедупа. Возвращает (добавлено, пропущено)."""
        added = skipped = 0
        now = _now()
        for raw in usernames:
            username = normalize_username(raw)
            if not username:
                skipped += 1
                continue
            if self.find(username=username):
                skipped += 1
                continue
            # у импортированных нет telegram id — используем отрицательный суррогат,
            # он будет заменён на реальный id при первой встрече канала в поиске
            surrogate = -abs(hash(username)) % (2**62) - 1
            self.conn.execute(
                """INSERT INTO channels
                   (channel_id, username, link, status, stream, method,
                    first_seen, last_checked)
                   VALUES (?, ?, ?, ?, 'imported', 'import', ?, ?)""",
                (surrogate, username, f"https://t.me/{username}", STATUS_IMPORTED, now, now),
            )
            added += 1
        self.conn.commit()
        return added, skipped

    def mark_exported(self, channel_ids: Iterable[int]) -> None:
        ids = list(channel_ids)
        if not ids:
            return
        now = _now()
        self.conn.executemany(
            """UPDATE channels SET status = ?, exported_at = ?
               WHERE channel_id = ? AND status NOT IN (?, ?)""",
            [(STATUS_EXPORTED, now, cid, STATUS_BLACKLIST, STATUS_IMPORTED) for cid in ids],
        )
        self.conn.commit()

    # --- чтение -----------------------------------------------------------

    def export_rows(
        self,
        stream: Optional[str] = None,
        new_only: bool = False,
        min_score: int = 0,
        include_no_contact: bool = True,
        only_no_contact: bool = False,
        since: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        """Каналы для выгрузки: прошедшие фильтры, без blacklist, по убыванию score."""
        sql = [
            "SELECT * FROM channels",
            "WHERE passed_filters = 1 AND blacklist_hit = 0 AND status != ?",
        ]
        params: list[Any] = [STATUS_IMPORTED]

        if stream:
            sql.append("AND stream = ?")
            params.append(stream)
        if new_only:
            sql.append("AND status = ?")
            params.append(STATUS_NEW)
        if min_score:
            sql.append("AND score >= ?")
            params.append(min_score)
        if only_no_contact:
            sql.append("AND (contact IS NULL OR contact = '')")
        elif not include_no_contact:
            sql.append("AND contact IS NOT NULL AND contact != ''")
        if since:
            sql.append("AND first_seen >= ?")
            params.append(since)

        sql.append("ORDER BY score DESC, subscribers DESC")
        return list(self.conn.execute(" ".join(sql), params).fetchall())

    def stale_channels(self, older_than_days: int = 30, limit: int = 200) -> list[sqlite3.Row]:
        """Каналы, у которых метрики старше N дней — для фонового пересканирования."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        return list(
            self.conn.execute(
                """SELECT * FROM channels
                   WHERE last_checked < ? AND blacklist_hit = 0 AND status != ?
                   ORDER BY last_checked ASC LIMIT ?""",
                (cutoff, STATUS_IMPORTED, limit),
            ).fetchall()
        )

    def stats(self) -> dict[str, Any]:
        """Сводка базы для команды `stats`."""
        cur = self.conn
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        total = cur.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
        new_week = cur.execute(
            "SELECT COUNT(*) c FROM channels WHERE first_seen >= ?", (week_ago,)
        ).fetchone()["c"]
        by_stream = {
            row["stream"]: row["c"]
            for row in cur.execute(
                "SELECT stream, COUNT(*) c FROM channels GROUP BY stream ORDER BY c DESC"
            )
        }
        by_status = {
            row["status"]: row["c"]
            for row in cur.execute(
                "SELECT status, COUNT(*) c FROM channels GROUP BY status ORDER BY c DESC"
            )
        }
        blacklisted = cur.execute(
            "SELECT COUNT(*) c FROM channels WHERE blacklist_hit = 1"
        ).fetchone()["c"]
        no_contact = cur.execute(
            """SELECT COUNT(*) c FROM channels
               WHERE (contact IS NULL OR contact = '') AND passed_filters = 1"""
        ).fetchone()["c"]
        exportable = cur.execute(
            """SELECT COUNT(*) c FROM channels
               WHERE passed_filters = 1 AND blacklist_hit = 0 AND status != ?""",
            (STATUS_IMPORTED,),
        ).fetchone()["c"]
        runs = list(
            cur.execute(
                """SELECT stream, started_at, finished_at, status, checked, new_channels, note
                   FROM runs ORDER BY id DESC LIMIT 5"""
            )
        )
        return {
            "total": total,
            "new_last_week": new_week,
            "by_stream": by_stream,
            "by_status": by_status,
            "blacklisted": blacklisted,
            "no_contact": no_contact,
            "exportable": exportable,
            "recent_runs": [dict(r) for r in runs],
        }

    # --- история обходов --------------------------------------------------

    def start_run(self, stream: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (stream, started_at) VALUES (?, ?)", (stream, _now())
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, status: str, checked: int, new_channels: int, note: str = ""
    ) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at = ?, status = ?, checked = ?,
               new_channels = ?, note = ? WHERE id = ?""",
            (_now(), status, checked, new_channels, note, run_id),
        )
        self.conn.commit()

    def log_candidate(
        self, run_id: Optional[int], key: str, method: str, source: Optional[str]
    ) -> None:
        self.conn.execute(
            """INSERT INTO crawl_log (run_id, channel_key, method, source_channel, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, key, method, source, _now()),
        )
        self.conn.commit()

    def seed_channels_for_mentions(self, stream: str, limit: int = 50) -> list[str]:
        """Каналы из базы, от которых можно строить граф упоминаний."""
        rows = self.conn.execute(
            """SELECT username FROM channels
               WHERE stream = ? AND username IS NOT NULL AND blacklist_hit = 0
               ORDER BY score DESC LIMIT ?""",
            (stream, limit),
        ).fetchall()
        return [r["username"] for r in rows if r["username"]]


def _resolve_status(
    metrics: dict[str, Any], passed: bool, existing: Optional[sqlite3.Row]
) -> str:
    """Статус канала: blacklist > no_contact > exported (сохраняем) > new/filtered."""
    if metrics.get("blacklist_hit"):
        return STATUS_BLACKLIST
    if not passed:
        return "filtered"
    if not metrics.get("contact"):
        return STATUS_NO_CONTACT
    # уже выгруженный канал не возвращаем в «новые» при перепроверке
    if existing and existing["status"] == STATUS_EXPORTED:
        return STATUS_EXPORTED
    return STATUS_NEW


def normalize_username(raw: str) -> Optional[str]:
    """`@name`, `t.me/name`, `https://t.me/name?x=1`, `name` -> `name`."""
    if not raw:
        return None
    value = raw.strip().strip('"\'')
    if not value or value.startswith("#"):
        return None
    value = value.split("?")[0].split("#")[0]
    for prefix in ("https://", "http://", "tg://resolve?domain="):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    value = value.lstrip("@")
    if value.lower().startswith("t.me/"):
        value = value[5:]
    elif value.lower().startswith("telegram.me/"):
        value = value[12:]
    value = value.strip("/").split("/")[0]
    if not value or not value.replace("_", "").isalnum():
        return None
    return value
