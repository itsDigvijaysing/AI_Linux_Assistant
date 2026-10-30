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
        self.assertEqual((s.get("speed"), s.get("small_on")), ("efficient", True))
        self.assertEqual((s.get_for("charger", "speed"), s.get_for("charger", "small_on")), ("auto", False))
        s.set_on_ac(True)
        self.assertEqual((s.active_profile(), s.get("speed"), s.get("small_on")), ("charger", "auto", False))
        self.assertEqual(s.get("small_model"), "jiwo-0.8b")  # one choice for both

    def test_set_writes_the_active_profile_only(self):
        s = self.make(on_ac=False)
        s.set("voice", "supertonic:M4")
        self.assertEqual(s.get_for("battery", "voice"), "supertonic:M4")
        self.assertEqual(s.get_for("charger", "voice"), "supertonic:F1")
        self.assertEqual(json.loads(self.path.read_text()), {"battery.voice": "supertonic:M4"})

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
        self.assertIn("speed", seen)
        self.assertIn("small_on", seen)
        self.assertNotIn("small_model", seen)
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
        self.assertEqual(s.get("small_model"), "jiwo-0.8b")
        stored = json.loads(self.path.read_text())
        self.assertNotIn("power_mode", stored)
        self.assertNotIn("voice", stored)

    def test_view_edits_its_profile(self):
        s = self.make(on_ac=False)
        v = ProfileView(s, "charger")
        seen = []
        v.connect("changed", lambda _v, k: seen.append(k))
        v.set("speed", "full")
        self.assertEqual(s.get("speed"), "efficient")  # battery is in use
        self.assertEqual(s.get_for("charger", "speed"), "full")
        self.assertEqual(seen, ["speed"])
        seen.clear()
        s.set("voice", "supertonic:M4")  # battery's value: not the edited profile
        self.assertEqual(seen, [])
        v.select("battery")
        self.assertEqual(v.get("voice"), "supertonic:M4")
        self.assertIn("voice", seen)
        self.assertIn("speed", seen)
        seen.clear()
        s.set("wake_word", "jarvis")  # global keys pass through
        self.assertEqual(seen, ["wake_word"])
        v.close()


    def test_migrates_per_profile_models_to_one_choice(self):
        s = self.make(data={"battery.small_model": "jiwo-0.8b", "charger.small_model": "none",
                            "charger.big_model": "gemma4:e4b", "battery.big_model_policy": "instant",
                            "charger.ollama_keep_alive": "10m", "battery.voice": "supertonic:M4"})
        self.assertEqual(s.get("small_model"), "jiwo-0.8b")
        self.assertEqual(s.get("big_model"), "gemma4:e4b")
        self.assertEqual(s.get_for("battery", "speed"), "full")  # always loaded was Max Performance
        self.assertFalse(s.get_for("charger", "small_on"))
        self.assertEqual(json.loads(self.path.read_text()), {
            "small_model": "jiwo-0.8b", "big_model": "gemma4:e4b", "battery.speed": "full",
            "battery.voice": "supertonic:M4"})  # only user changes stay stored

    def test_migrates_small_none_to_off(self):
        s = self.make(data={"battery.small_model": "none"})
        self.assertFalse(s.get_for("battery", "small_on"))
        self.assertEqual(s.get("small_model"), "jiwo-0.8b")  # turning it back on uses the default
        s = self.make(data={"small_model": "none", "load_mode": "full"})
        self.assertFalse(s.get_for("battery", "small_on"))
        self.assertEqual((s.get_for("battery", "speed"), s.get_for("charger", "speed")), ("full", "full"))
        self.assertNotIn("small_model", json.loads(self.path.read_text()))


    def test_engine_gets_the_profiles_speed_and_small_model(self):
        from ai_linux.engine_client import EngineClient

        s = self.make(on_ac=False)
        client = EngineClient.__new__(EngineClient)
        client.settings = s
        self.assertEqual(client.model_values(), {"small_model": "jiwo-0.8b", "big_model_policy": "on_demand",
                                                 "ollama_keep_alive": "2m"})
        s.set_on_ac(True)  # charger defaults: Automatic, small model off
        self.assertEqual(client.model_values(), {"small_model": "none", "big_model_policy": "on_demand",
                                                 "ollama_keep_alive": "30m"})
        s.set("speed", "full")
        self.assertEqual(client.model_values()["big_model_policy"], "instant")
        self.assertEqual(s.get_for("battery", "speed"), "efficient")

    def test_running_now_follows_the_profile(self):
        from ai_linux.ui.pages.models_overview import running_rows

        s = self.make(on_ac=True)
        rows = running_rows(s, {"running": True, "loaded": []})
        self.assertEqual(rows[0][1], "Charger settings, Automatic")
        self.assertTrue(rows[2][2].startswith("Off on the charger"))
        s.set_on_ac(False)
        rows = running_rows(s, {"running": True, "loaded": []})
        self.assertEqual(rows[0][1], "Battery settings, Save Battery")
        self.assertIn("picks the command", rows[2][2])
        self.assertIn("Asleep", rows[3][2])


if __name__ == "__main__":
    unittest.main()
