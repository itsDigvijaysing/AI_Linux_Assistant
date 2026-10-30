"""Live words and the reply on the app side: engine events reach the bus; the theme and speech choices."""

from __future__ import annotations

import importlib.util
import sys
import unittest

if importlib.util.find_spec("gi") is None:
    raise unittest.SkipTest("needs PyGObject (python3-gi)")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine_client import EngineClient  # noqa: E402
from ai_linux.schema import CHOICES, DEFAULTS, PROFILE_KEYS  # noqa: E402
from ai_linux.state import Bus, Turn  # noqa: E402


class BusTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        self.seen = []
        self.bus.connect("live-text", lambda _b, *args: self.seen.append(("live",) + args))
        self.bus.connect("saying", lambda _b, text, secs: self.seen.append(("saying", text, secs)))

    def test_engine_events_reach_the_bus(self):
        client = EngineClient.__new__(EngineClient)
        client.bus = self.bus
        client._event({"ev": "partial", "text": "open the", "stable": 1, "final": False})
        client._event({"ev": "saying", "text": "Opening Files.", "seconds": 1.2})
        client._event({"ev": "partial"})
        self.assertEqual(self.seen, [("live", "open the", 1, False), ("saying", "Opening Files.", 1.2),
                                     ("live", "", 0, False)])

    def test_last_words_are_kept_until_the_turn(self):
        self.bus.live_text("open the files", 3, True)
        self.assertEqual(self.bus.live, ("open the files", 3, True))
        self.bus.add_turn(Turn(heard="open the files", reply="Done."))
        self.assertEqual(self.bus.live, ("", 0, True))


class ChoiceTests(unittest.TestCase):
    def test_dark_theme_and_speech_device(self):
        self.assertIn("dark", CHOICES["theme"])
        self.assertEqual(CHOICES["stt_device"], ("intel-gpu", "cpu"))
        self.assertEqual((DEFAULTS["stt_device"], DEFAULTS["live_text"]), ("intel-gpu", True))
        self.assertNotIn("stt_device", PROFILE_KEYS)  # one choice for battery and the charger

    def test_theme_maps_to_the_colour_scheme(self):
        import gi

        gi.require_version("Adw", "1")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Adw

        from ai_linux.ui.theme import color_scheme

        self.assertEqual(color_scheme("light"), Adw.ColorScheme.FORCE_LIGHT)
        self.assertEqual(color_scheme("dark"), Adw.ColorScheme.FORCE_DARK)
        self.assertEqual(color_scheme("system"), Adw.ColorScheme.DEFAULT)


if __name__ == "__main__":
    unittest.main()
