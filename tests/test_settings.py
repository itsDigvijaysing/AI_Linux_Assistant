"""Settings: battery/charger profiles, the page view, and migration from single values."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import importlib.util  # noqa: E402
if importlib.util.find_spec("gi") is None:
    raise unittest.SkipTest("needs PyGObject (python3-gi)")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.config import ProfileView, Settings  # noqa: E402


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "app.json"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, on_ac=False, data=None):
        if data is not None:
            self.path.write_text(json.dumps(data))
        return Settings(self.path, on_ac=on_ac)

    def test_defaults_differ_per_profile(self):
        s = self.make(on_ac=False)
        self.assertEqual(s.active_profile(), "battery")
        self.assertEqual(s.get("small_model"), "jiwo-0.8b")
        self.assertEqual(s.get_for("charger", "small_model"), "none")
        s.set_on_ac(True)
        self.assertEqual((s.active_profile(), s.get("small_model")), ("charger", "none"))

    def test_set_writes_the_active_profile_only(self):
        s = self.make(on_ac=False)
        s.set("voice", "supertonic:F1")
        self.assertEqual(s.get_for("battery", "voice"), "supertonic:F1")
        self.assertEqual(s.get_for("charger", "voice"), "supertonic:M1")
        self.assertEqual(json.loads(self.path.read_text()), {"battery.voice": "supertonic:F1"})

    def test_signals(self):
        s = self.make(on_ac=False)
        seen = []
        s.connect("changed", lambda _s, k: seen.append(k))
        s.connect("profile-changed", lambda _s, p: seen.append(f"profile={p}"))
        s.set_for("charger", "voice", "supertonic:F2")  # not in use: only the stored key fires
        self.assertEqual(seen, ["charger.voice"])
        seen.clear()
        s.set_on_ac(True)  # the effective value of every differing key changes
        self.assertIn("profile=charger", seen)
        self.assertIn("voice", seen)
        self.assertIn("small_model", seen)
        self.assertNotIn("stt_engine", seen)
        seen.clear()
        s.set("profile", "battery")  # forcing a profile switches too
        self.assertEqual(s.active_profile(), "battery")
        self.assertIn("profile", seen)
        self.assertIn("voice", seen)

    def test_global_keys_unchanged(self):
        s = self.make()
        s.set("wake_word", "jarvis")
        self.assertEqual(s.get("wake_word"), "jarvis")
        with self.assertRaises(ValueError):
            s.set("big_device", "nvidia")
        with self.assertRaises(KeyError):
            s.set_for("battery", "wake_word", "x")

    def test_migrates_single_values(self):
        s = self.make(data={"power_mode": "performance", "voice": "supertonic:F1", "small_model": "jiwo-0.8b",
                            "wake_word": "jarvis"})
        self.assertEqual(s.get("profile"), "charger")
        self.assertEqual(s.get_for("battery", "voice"), "supertonic:F1")
        self.assertEqual(s.get_for("charger", "voice"), "supertonic:F1")
        self.assertEqual(s.get_for("charger", "small_model"), "none")  # the old value moves to battery only
        stored = json.loads(self.path.read_text())
        self.assertNotIn("power_mode", stored)
        self.assertNotIn("voice", stored)

    def test_view_edits_its_profile(self):
        s = self.make(on_ac=False)
        v = ProfileView(s, "charger")
        seen = []
        v.connect("changed", lambda _v, k: seen.append(k))
        v.set("big_model", "gemma4:e4b")
        self.assertEqual(s.get("big_model"), "qwen3.5:4b")  # battery is in use
        self.assertEqual(s.get_for("charger", "big_model"), "gemma4:e4b")
        self.assertEqual(seen, ["big_model"])
        seen.clear()
        s.set("voice", "supertonic:F1")  # battery's value: not the edited profile
        self.assertEqual(seen, [])
        v.select("battery")
        self.assertEqual(v.get("voice"), "supertonic:F1")
        self.assertIn("voice", seen)
        self.assertIn("big_model", seen)
        seen.clear()
        s.set("wake_word", "jarvis")  # global keys pass through
        self.assertEqual(seen, ["wake_word"])
        v.close()


if __name__ == "__main__":
    unittest.main()
