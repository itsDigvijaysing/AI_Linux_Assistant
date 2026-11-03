"""Home's New Session and Delete This Session, with its Undo."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.controller import Controller  # noqa: E402
from ai_linux.state import Bus, Turn  # noqa: E402


class FakeHistory:
    def __init__(self, turns):
        self.turns = {t.id: t for t in turns}

    def delete(self, turn_id):
        return self.turns.pop(turn_id, None) is not None

    def add(self, turn):
        self.turns[turn.id] = turn


class FakeSettings:
    def get(self, key):
        return None


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        self.controller = Controller(FakeSettings(), self.bus)
        self.cleared = []
        self.bus.connect("session-cleared", lambda *_: self.cleared.append(self.bus.session_start))
        self.old = Turn(id="old", ts=10.0, heard="earlier")
        self.mine = [Turn(id="a", ts=20.0, heard="one"), Turn(id="b", ts=21.0, heard="two")]
        self.history = FakeHistory([self.old, *self.mine])

    def test_delete_removes_only_the_session_and_starts_fresh(self):
        self.bus.session_start = 15.0
        self.controller.delete_session(self.history, self.mine)
        self.assertEqual(set(self.history.turns), {"old"})
        self.assertGreater(self.bus.session_start, 21.0)
        self.assertEqual(len(self.cleared), 1)

    def test_undo_brings_the_turns_back_on_home(self):
        self.bus.session_start = 15.0
        self.controller.delete_session(self.history, self.mine)
        self.controller.restore_session(self.history, self.mine, 15.0)
        self.assertEqual(set(self.history.turns), {"old", "a", "b"})
        self.assertEqual(self.bus.session_start, 15.0)
        self.assertEqual(self.cleared[-1], 15.0)

    def test_wake_missed_signal(self):
        seen = []
        self.bus.connect("wake-missed", lambda *_: seen.append(True))
        self.bus.wake_missed()
        self.assertEqual(seen, [True])


class TurnRecordTests(unittest.TestCase):
    def test_keeps_router_guesses_and_ignores_unknown_fields(self):
        t = Turn.from_dict({"heard": "volume", "alts": {"set_volume": 0.6}, "status": "wrong", "future": 1})
        self.assertEqual((t.alts, t.status), ({"set_volume": 0.6}, "wrong"))
        self.assertEqual(Turn.from_dict(t.to_dict()), t)


if __name__ == "__main__":
    unittest.main()


class TurnFromEngineTests(unittest.TestCase):
    def test_newer_tool_fields_do_not_drop_the_turn(self):
        t = Turn.from_dict({"heard": "volume up", "tools": [{"name": "set_volume", "args": {}, "took": 0.1}],
                            "extra": 1})
        self.assertEqual((t.heard, t.tools[0].name), ("volume up", "set_volume"))


class EngineClientStartTests(unittest.TestCase):
    def test_failed_start_closes_its_log_file(self):
        import tempfile
        from unittest import mock

        from ai_linux import engine_client
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        opened = []
        real_open = open

        def spy(*a, **k):
            f = real_open(*a, **k)
            opened.append(f)
            return f
        client = engine_client.EngineClient.__new__(engine_client.EngineClient)
        client.python, client.proc, client.bus = "/nonexistent/python", None, mock.Mock()
        with mock.patch.object(engine_client, "LOG_DIR", Path(tmp.name)), \
                mock.patch("builtins.open", spy), \
                mock.patch.object(engine_client.subprocess, "Popen", side_effect=OSError("no such file")):
            client.start()
        self.assertTrue(opened and all(f.closed for f in opened))


class EngineLogTests(unittest.TestCase):
    """engine.log is the engine's stderr, copied by the app: it rotates like app.log instead of growing forever."""

    def setUp(self):
        import tempfile
        from unittest import mock

        from ai_linux import engine_client
        self.ec = engine_client
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("LOG_DIR", self.dir), ("ENGINE_LOG_BYTES", 2000), ("ENGINE_LOG_BACKUPS", 2)):
            patcher = mock.patch.object(engine_client, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = engine_client.EngineClient.__new__(engine_client.EngineClient)
        from collections import deque
        self.client._tail = deque(maxlen=20)

    def copy(self, lines):
        from unittest import mock
        proc = mock.Mock(stderr=iter(lines))
        self.client._log(proc, self.ec.engine_log())

    def test_the_log_rotates_and_keeps_a_few_backups(self):
        self.copy([f"line {i:04d} " + "x" * 80 + "\n" for i in range(500)])
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ["engine.log", "engine.log.1", "engine.log.2"])
        for p in self.dir.iterdir():
            self.assertLessEqual(p.stat().st_size, 2000)
        self.assertTrue((self.dir / "engine.log").read_text().rstrip().endswith("line 0499 " + "x" * 80))
        self.assertEqual(self.client._tail[-1], "line 0499 " + "x" * 80)

    def test_lines_are_copied_as_they_are(self):
        self.copy(["Traceback (most recent call last):\n", "  100% done, café\n"])
        self.assertEqual((self.dir / "engine.log").read_text(encoding="utf-8"),
                         "Traceback (most recent call last):\n  100% done, café\n")

    def test_a_new_start_appends_to_the_log(self):
        (self.dir / "engine.log").write_text("before\n")
        self.copy(["after\n"])
        self.assertEqual((self.dir / "engine.log").read_text(), "before\nafter\n")
