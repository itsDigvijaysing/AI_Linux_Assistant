"""Timers and reminders. Each rings once (sound, notification, the message spoken) and is kept in a file, so one
set before a restart still rings; one missed by under an hour while the app was closed rings at start."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

MISSED_S = 3600  # rings late if missed by at most this, else it is dropped
MAX_S = 7 * 24 * 3600
CHECK_S = 30.0  # waits are monotonic and pause in suspend, so look at the wall clock this often


def spoken_span(seconds: float) -> str:
    s = int(round(seconds))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "", f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{sec} second{'s' * (sec != 1)}" if sec and not h else ""]
    parts = [p for p in parts if p]
    return " and ".join(parts) if parts else "a moment"


class Timers:
    def __init__(self, path: Path, ring: Callable[[str], None], clock: Callable[[], float] = time.time):
        self.path, self.ring, self.clock = path, ring, clock
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()  # a timer firing and a new one being set may save at once
        self._items: dict[str, dict] = {}  # id -> {"id", "due": epoch s, "message", "length": s asked for}
        self._threads: dict[str, threading.Timer] = {}

    def start(self) -> None:
        try:
            items = json.loads(self.path.read_text())
        except (OSError, ValueError):
            items = []
        now = self.clock()
        for it in items if isinstance(items, list) else []:
            try:
                due, msg = float(it["due"]), str(it.get("message") or "")
            except (KeyError, TypeError, ValueError):
                continue
            if due >= now - MISSED_S:
                self._schedule(str(it.get("id") or uuid.uuid4().hex), due, msg, int(it.get("length") or 0))
        self._save()

    def add(self, seconds: float, message: str = "") -> dict:
        seconds = max(1.0, min(float(seconds), MAX_S))
        tid = uuid.uuid4().hex[:8]
        self._schedule(tid, self.clock() + seconds, message.strip(), int(round(seconds)))
        self._save()
        return self._items[tid]

    def cancel(self, ids: list[str] | None = None) -> int:
        """Cancels these timers (default every one); returns how many."""
        with self._lock:
            gone = [tid for tid in self._items if ids is None or tid in ids]
            for tid in gone:
                self._items.pop(tid, None)
                t = self._threads.pop(tid, None)
                if t is not None:
                    t.cancel()
        self._save()
        return len(gone)

    def pending(self) -> list[dict]:
        with self._lock:
            return sorted(self._items.values(), key=lambda it: it["due"])

    def close(self) -> None:
        with self._lock:
            for t in self._threads.values():
                t.cancel()

    def _schedule(self, tid: str, due: float, message: str, length: int = 0) -> None:
        with self._lock:
            self._items[tid] = {"id": tid, "due": due, "message": message, "length": length}
        self._arm(tid)

    def _arm(self, tid: str) -> None:
        with self._lock:
            it = self._items.get(tid)
            if it is None:  # cancelled meanwhile
                return
            t = threading.Timer(min(max(0.0, it["due"] - self.clock()), CHECK_S), self._fire, args=(tid,))
            t.daemon = True
            self._threads[tid] = t
            t.start()

    def _fire(self, tid: str) -> None:
        with self._lock:
            it = self._items.get(tid)
            early = it is not None and it["due"] - self.clock() > 0.05
            if not early:
                self._items.pop(tid, None)
                self._threads.pop(tid, None)
        if early:
            self._arm(tid)
            return
        self._save()
        if it is not None:
            try:
                self.ring(it["message"])
            except Exception:
                log.exception("ringing a timer")

    def _save(self) -> None:
        with self._save_lock:
            with self._lock:
                data = json.dumps(list(self._items.values()))
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(data)
                os.replace(tmp, self.path)
            except OSError:
                log.exception("saving timers")
