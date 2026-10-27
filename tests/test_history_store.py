"""HistoryStore on a throwaway directory: run with /usr/bin/python3 -m unittest discover -s tests."""

from __future__ import annotations

import datetime as dt
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.history_store import HistoryStore  # noqa: E402
from ai_linux.state import ToolCall, Turn  # noqa: E402


def _turn(heard: str, ts: float, **kw) -> Turn:
    return Turn(heard=heard, ts=ts, **kw)


class HistoryStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "data" / "ai-linux"
        self.store = HistoryStore(self.dir / "history.db")
        self.now = time.time()

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_private_permissions(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / "history.db").st_mode), 0o600)

    def test_wal_mode(self):
        mode = self.store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_round_trip_keeps_tools_and_latency(self):
        t = _turn("delete everything", self.now, reply="I won't.", route="big", model="qwen3.5:4b",
                  confidence=0.88, latency={"asr": 0.2, "llm": 1.1}, source="voice",
                  tools=[ToolCall("run_command", {}, "rm -rf ~/*", "blocked", None, "catastrophic")])
        self.store.add(t)
        got = self.store.get(t.id)
        self.assertEqual(got, t)
        self.assertIsInstance(got.tools[0], ToolCall)
        self.assertEqual(got.tools[0].verdict, "blocked")

    def test_update_changes_row_and_upserts(self):
        t = _turn("open files", self.now)
        self.store.add(t)
        t.reply, t.status = "Opening Files.", "partial"
        self.store.update(t)
        got = self.store.get(t.id)
        self.assertEqual((got.reply, got.status), ("Opening Files.", "partial"))
        fresh = _turn("never added", self.now)
        self.store.update(fresh)
        self.assertIsNotNone(self.store.get(fresh.id))
        self.assertEqual(self.store.count(), 2)

    def test_recent_is_newest_first_with_paging(self):
        for i in range(5):
            self.store.add(_turn(f"t{i}", self.now - 100 + i))
        recent = self.store.recent(limit=3)
        self.assertEqual([t.heard for t in recent], ["t4", "t3", "t2"])
        older = self.store.recent(limit=10, before=recent[-1].ts)
        self.assertEqual([t.heard for t in older], ["t1", "t0"])

    def test_search_is_case_insensitive_over_heard_and_reply(self):
        self.store.add(_turn("Open FILES please", self.now - 3))
        self.store.add(_turn("volume up", self.now - 2, reply="Volume at 60 Percent"))
        self.store.add(_turn("Ölüdeniz weather", self.now - 1))
        self.store.add(_turn("100% sure_thing", self.now))
        self.assertEqual([t.heard for t in self.store.search("files")], ["Open FILES please"])
        self.assertEqual([t.heard for t in self.store.search("percent")], ["volume up"])
        self.assertEqual([t.heard for t in self.store.search("ÖLÜ")], ["Ölüdeniz weather"])
        # LIKE wildcards must be literal
        self.assertEqual([t.heard for t in self.store.search("0%")], ["100% sure_thing"])
        self.assertEqual(self.store.search("1%g"), [])
        self.assertEqual(self.store.search("p_n"), [])
        self.assertEqual(len(self.store.search("  ")), 4)

    def test_days_and_for_day(self):
        today = dt.date.today()
        noon = dt.datetime.combine(today, dt.time(12)).timestamp()
        yesterday_noon = noon - 86400
        self.store.add(_turn("b", noon + 60))
        self.store.add(_turn("a", noon))
        self.store.add(_turn("y", yesterday_noon))
        self.assertEqual(self.store.days(), [(today, 2), (today - dt.timedelta(days=1), 1)])
        self.assertEqual([t.heard for t in self.store.for_day(today)], ["a", "b"])
        self.assertEqual([t.heard for t in self.store.for_day(today - dt.timedelta(days=1))], ["y"])
        self.assertEqual(self.store.for_day(today - dt.timedelta(days=5)), [])

    def test_delete_clear_prune(self):
        old = _turn("old", self.now - 40 * 86400)
        mid = _turn("mid", self.now - 10 * 86400)
        new = _turn("new", self.now)
        for t in (old, mid, new):
            self.store.add(t)
        self.assertEqual(self.store.prune(0), 0)
        self.assertEqual(self.store.prune(30), 1)
        self.assertIsNone(self.store.get(old.id))
        self.assertTrue(self.store.delete(mid.id))
        self.assertFalse(self.store.delete(mid.id))
        self.assertEqual(self.store.clear(), 1)
        self.assertEqual(self.store.recent(), [])

    def test_listeners(self):
        events = []
        listener = lambda e, i: events.append((e, i))  # noqa: E731
        self.store.add_listener(listener)
        t = _turn("x", self.now)
        self.store.add(t)
        self.store.update(t)
        self.store.delete(t.id)
        self.store.clear()
        self.store.remove_listener(listener)
        self.store.add(_turn("y", self.now))
        self.assertEqual(events, [("add", t.id), ("update", t.id), ("delete", t.id), ("clear", None)])

    def test_bad_json_falls_back_to_columns(self):
        t = _turn("open files", self.now, reply="ok", route="small")
        self.store.add(t)
        with self.store._conn:
            self.store._conn.execute("UPDATE turns SET data='not json' WHERE id=?", (t.id,))
        got = self.store.get(t.id)
        self.assertEqual((got.heard, got.reply, got.route), ("open files", "ok", "small"))

    def test_unknown_json_keys_are_ignored(self):
        t = _turn("open files", self.now)
        self.store.add(t)
        data = '{"heard": "open files", "future_field": 1, "tools": [{"name": "x", "new": 2}]}'
        with self.store._conn:
            self.store._conn.execute("UPDATE turns SET data=? WHERE id=?", (data, t.id))
        got = self.store.get(t.id)
        self.assertEqual(got.tools[0].name, "x")

    def test_threads(self):
        def worker(n):
            for i in range(25):
                self.store.add(_turn(f"{n}-{i}", self.now + n * 100 + i))
                self.store.recent(5)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(self.store.count(), 100)

    def test_reopen_existing(self):
        self.store.add(_turn("persist", self.now))
        self.store.close()
        self.store = HistoryStore(self.dir / "history.db")
        self.assertEqual([t.heard for t in self.store.recent()], ["persist"])


if __name__ == "__main__":
    unittest.main()
