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
