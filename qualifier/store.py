"""SQLite: кэш данных площадок и LLM-ответов на CACHE_TTL_DAYS + история проверок."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    partner TEXT NOT NULL,
    links TEXT NOT NULL,
    primary_platform TEXT,
    followers INTEGER,
    median_views INTEGER,
    er REAL,
    geo TEXT,
    geo_confidence TEXT,
    affiliate_type TEXT,
    tier REAL,
    cpa REAL,
    flags TEXT,
    manual_review INTEGER,
    report_path TEXT,
    card_json TEXT
);
CREATE INDEX IF NOT EXISTS checks_created ON checks(created_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str | Path, ttl_days: int = 7, clock=_now):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.ttl = timedelta(days=ttl_days)
        self.clock = clock

    def close(self) -> None:
        self.conn.close()

    # --- кэш --------------------------------------------------------------

    def cache_get(self, kind: str, key: str) -> Optional[Any]:
        row = self.conn.execute(
            "SELECT created_at, payload FROM cache WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        if row is None:
            return None
        created = datetime.fromisoformat(row["created_at"])
        if self.clock() - created > self.ttl:
            return None
        return json.loads(row["payload"])

    def cache_age(self, kind: str, key: str) -> Optional[datetime]:
        row = self.conn.execute(
            "SELECT created_at FROM cache WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        return datetime.fromisoformat(row["created_at"]) if row else None

    def cache_set(self, kind: str, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cache (kind, key, created_at, payload) VALUES (?, ?, ?, ?)",
            (kind, key, self.clock().isoformat(), json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def cache_purge(self) -> int:
        cutoff = (self.clock() - self.ttl).isoformat()
        cur = self.conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    # --- история ------------------------------------------------------------

    def save_check(self, row: dict[str, Any]) -> int:
        columns = [
            "created_at", "partner", "links", "primary_platform", "followers", "median_views",
            "er", "geo", "geo_confidence", "affiliate_type", "tier", "cpa", "flags",
            "manual_review", "report_path", "card_json",
        ]
        values = [row.get(c) for c in columns]
        cur = self.conn.execute(
            f"INSERT INTO checks ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            values,
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def history(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = (self.clock() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT id, created_at, partner, links, primary_platform, followers, median_views, er, "
            "geo, geo_confidence, affiliate_type, tier, cpa, flags, manual_review, report_path "
            "FROM checks WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
