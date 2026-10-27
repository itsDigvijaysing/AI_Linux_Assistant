"""Conversation history on sqlite3: one row per Turn, private to the user."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from ai_linux.state import ToolCall, Turn

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    heard TEXT,
    reply TEXT,
    route TEXT,
    model TEXT,
    confidence REAL,
    status TEXT,
    source TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
"""

_COLUMNS = ("id", "ts", "heard", "reply", "route", "model", "confidence", "status", "source", "data")
_TURN_FIELDS = {f.name for f in dataclasses.fields(Turn)}
_TOOL_FIELDS = {f.name for f in dataclasses.fields(ToolCall)}


def _fold(text) -> str:
    # sqlite's lower() and LIKE only fold ASCII; casefold handles every script.
    return text.casefold() if isinstance(text, str) else ""


def _day_bounds(day: dt.date) -> tuple[float, float]:
    start = dt.datetime.combine(day, dt.time.min).timestamp()
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp()
    return start, end


def _turn_from_row(row: sqlite3.Row) -> Turn:
    """Prefer the JSON copy; fall back to columns so a schema change never loses a row."""
    try:
        d = json.loads(row["data"] or "{}")
        d = {k: v for k, v in d.items() if k in _TURN_FIELDS}
        d["tools"] = [ToolCall(**{k: v for k, v in t.items() if k in _TOOL_FIELDS})
                      for t in d.get("tools") or [] if isinstance(t, dict) and "name" in t]
        d.setdefault("heard", row["heard"] or "")
        d["id"], d["ts"] = row["id"], row["ts"]
        return Turn(**d)
    except (ValueError, TypeError) as exc:
        log.warning("history row %s: bad data (%s), using columns", row["id"], exc)
        return Turn(heard=row["heard"] or "", reply=row["reply"] or "", route=row["route"] or "none",
                    model=row["model"] or "", confidence=row["confidence"], status=row["status"] or "ok",
                    source=row["source"] or "voice", id=row["id"], ts=row["ts"])


class HistoryStore:
    """Thread-safe: one connection guarded by one lock, usable from any thread.

    Listeners get ``(event, turn_id)`` with event add | update | delete | clear | prune, on the
    calling thread, so UI listeners must marshal to the main loop themselves.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._listeners: list = []
        self._prepare_files()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function("fold", 1, _fold, deterministic=True)
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)

    def _prepare_files(self) -> None:
        # Conversations are private: 0700 dir, 0600 file (WAL and SHM files inherit the file mode).
        parent = self.path.parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
        try:
            os.chmod(parent, 0o700)
        except OSError as exc:
            log.warning("cannot restrict %s: %s", parent, exc)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)

    # listeners

    def add_listener(self, fn) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _notify(self, event: str, turn_id: str | None = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event, turn_id)
            except Exception:
                log.exception("history listener failed")

    # writes

    @staticmethod
    def _values(turn: Turn) -> tuple:
        data = json.dumps(turn.to_dict(), ensure_ascii=False, default=str)
        return (turn.id, float(turn.ts), turn.heard, turn.reply, turn.route, turn.model,
                turn.confidence, turn.status, turn.source, data)

    def _upsert(self, turn: Turn) -> None:
        sets = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS[1:])
        sql = (f"INSERT INTO turns ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))}) "
               f"ON CONFLICT(id) DO UPDATE SET {sets}")
        with self._lock, self._conn:
            self._conn.execute(sql, self._values(turn))

    def add(self, turn: Turn) -> None:
        self._upsert(turn)
        self._notify("add", turn.id)

    def update(self, turn: Turn) -> None:
        """Upsert, so an update for a turn that was never added still lands."""
        self._upsert(turn)
        self._notify("update", turn.id)

    def delete(self, turn_id: str) -> bool:
        with self._lock, self._conn:
            gone = self._conn.execute("DELETE FROM turns WHERE id=?", (turn_id,)).rowcount > 0
        if gone:
            self._notify("delete", turn_id)
        return gone

    def clear(self) -> int:
        with self._lock, self._conn:
            n = self._conn.execute("DELETE FROM turns").rowcount
        self._notify("clear")
        return n

    def prune(self, days: float) -> int:
        """Drop turns older than ``days``; zero or less keeps everything."""
        if not days or days <= 0:
            return 0
        cutoff = time.time() - float(days) * 86400
        with self._lock, self._conn:
            n = self._conn.execute("DELETE FROM turns WHERE ts < ?", (cutoff,)).rowcount
        if n:
            self._notify("prune")
        return n

    # reads

    def _query(self, sql: str, args: tuple = ()) -> list[Turn]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [_turn_from_row(r) for r in rows]

    def get(self, turn_id: str) -> Turn | None:
        found = self._query("SELECT * FROM turns WHERE id=?", (turn_id,))
        return found[0] if found else None

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT count(*) FROM turns").fetchone()[0]

    def recent(self, limit: int = 200, before: float | None = None) -> list[Turn]:
        """Newest first; pass the oldest ``ts`` seen as ``before`` to page further back."""
        if before is None:
            return self._query("SELECT * FROM turns ORDER BY ts DESC LIMIT ?", (int(limit),))
        return self._query("SELECT * FROM turns WHERE ts < ? ORDER BY ts DESC LIMIT ?", (before, int(limit)))

    def search(self, text: str, limit: int = 200) -> list[Turn]:
        """Case-insensitive substring match over what was heard and replied, newest first."""
        needle = _fold(text.strip())
        if not needle:
            return self.recent(limit)
        return self._query(
            "SELECT * FROM turns WHERE instr(fold(heard), ?) > 0 OR instr(fold(reply), ?) > 0 "
            "ORDER BY ts DESC LIMIT ?",
            (needle, needle, int(limit)),
        )

    def days(self) -> list[tuple[dt.date, int]]:
        """Local dates that have turns, newest first, with their turn counts."""
        with self._lock:
            rows = self._conn.execute("SELECT ts FROM turns ORDER BY ts DESC").fetchall()
        counts: dict[dt.date, int] = {}
        for (ts,) in rows:  # grouped in Python so the local timezone matches the UI's
            day = dt.date.fromtimestamp(ts)
            counts[day] = counts.get(day, 0) + 1
        return list(counts.items())

    def for_day(self, day: dt.date) -> list[Turn]:
        """Turns of one local day, oldest first (conversation order)."""
        start, end = _day_bounds(day)
        return self._query("SELECT * FROM turns WHERE ts >= ? AND ts < ? ORDER BY ts ASC", (start, end))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                log.exception("closing history")
