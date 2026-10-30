"""Home's timer list: the engine's list reaches the Bus, rows read "Rings at ...", cancel reaches the engine."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.state import Bus  # noqa: E402


class HomeTimerTests(unittest.TestCase):
    def test_engine_list_reaches_the_bus(self):
        from ai_linux.engine_client import EngineClient
        bus = Bus()
        seen = []
        bus.connect("timers-changed", lambda *_: seen.append(list(bus.timers)))
        client = EngineClient.__new__(EngineClient)
        client.bus = bus
        client._event({"ev": "timers", "items": [{"id": "a", "due": 100.0, "message": "tea", "length": 300}, 7]})
        self.assertEqual(seen, [[{"id": "a", "due": 100.0, "message": "tea", "length": 300}]])

    def test_row_text(self):
        from ai_linux.ui.pages.home import timer_text
        due = dt.datetime(2026, 10, 30, 17, 5).timestamp()
        self.assertEqual(timer_text({"due": due, "message": "stretch", "length": 300}),
                         ("Stretch", "Rings at 5:05 PM, set for 5 minutes"))
        self.assertEqual(timer_text({"due": due, "message": "", "length": 0}), ("Timer", "Rings at 5:05 PM"))

    def test_cancel_reaches_the_engine(self):
        from ai_linux.controller import Controller
        c = Controller(mock.Mock(), Bus())
        c.engine = mock.Mock()
        c.cancel_timer("a")
        c.engine.cancel_timer.assert_called_once_with("a")


if __name__ == "__main__":
    unittest.main()
