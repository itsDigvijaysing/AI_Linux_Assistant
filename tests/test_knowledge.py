"""Knowledge store, load modes and the readable shortcut text."""

from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import knowledge as kn  # noqa: E402
from ai_linux.hotkey import readable_trigger  # noqa: E402
from ai_linux.schema import is_big, loading  # noqa: E402

DAY = 86400.0


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "knowledge.json"
        self.k = kn.Knowledge(self.path)

    def test_add_dedupes_and_keeps(self):
        a, _ = self.k.add("I work night shifts")
        b, _ = self.k.add("  i work NIGHT shifts. ", kept=True)
        self.assertEqual(a.id, b.id)
        self.assertEqual([f.text for f in self.k.kept()], ["I work night shifts"])
        self.assertEqual(self.k.learned(), [])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_too_short_is_refused(self):
        fact, why = self.k.add("hi")
        self.assertIsNone(fact)
        self.assertTrue(why)

    def test_limits(self):
        for i in range(kn.KEPT_MAX):
            self.k.add(f"kept fact number {i}", kept=True)
        fact, why = self.k.add("one kept fact too many", kept=True)
        self.assertIsNone(fact)
        for i in range(kn.LEARNED_MAX + 3):
            self.k.add(f"learned fact number {i}")
        learned = self.k.learned()
        self.assertEqual(len(learned), kn.LEARNED_MAX)
        self.assertEqual(learned[0].text, f"learned fact number {kn.LEARNED_MAX + 2}")  # newest first

    def test_learned_facts_fade(self):
        fact, _ = self.k.add("I like tea")
        now = fact.updated
        self.assertEqual(fact.days_left(now), kn.LEARNED_DAYS)
        self.assertEqual(self.k.facts(now + kn.LEARNED_DAYS * DAY + 1), [])
        self.k.update(fact.id, kept=True)
        self.assertEqual(len(self.k.facts(now + 100 * DAY)), 1)

    def test_forget_best_match(self):
        self.k.add("I live in Pune", kept=True)
        self.k.add("My sister is called Asha")
        self.assertEqual(self.k.forget("forget that I live in Pune"), ["I live in Pune"])
        self.assertEqual(self.k.forget("something unrelated entirely"), [])
        self.assertEqual(len(self.k.facts()), 1)

    def test_prompt_is_capped_and_kept_first(self):
        self.k.add("I prefer short answers")
        self.k.add("I live in Pune", kept=True)
        prompt = self.k.prompt()
        self.assertTrue(prompt.startswith("Facts they keep: I live in Pune."))
        self.assertIn("may be out of date: I prefer short answers.", prompt)
        for i in range(kn.LEARNED_MAX):
            self.k.add(f"a long learned fact {i} " + "x" * 150)
        self.assertLessEqual(len(self.k.prompt()), kn.PROMPT_CHARS + 200)

    def test_two_processes_see_each_other(self):
        other = kn.Knowledge(self.path)
        self.k.add("I use a standing desk", kept=True)
        self.assertEqual([f.text for f in other.kept()], ["I use a standing desk"])

    def test_migrate_about_me(self):
        class S(dict):
            def get(self, k, d=None):
                return super().get(k, d)

            def reset(self, k):
                self.pop(k, None)
        s = S(about_me="I live in Pune. I have a cat.")
        kn.migrate_about_me(s, self.k)
        self.assertEqual([f.text for f in self.k.kept()], ["I live in Pune.", "I have a cat."])
        self.assertNotIn("about_me", s)

    def test_migrate_keeps_what_does_not_fit(self):
        class S(dict):
            def get(self, k, d=None):
                return super().get(k, d)

            def reset(self, k):
                self.pop(k, None)

            def set(self, k, v):
                self[k] = v
        self.k.add("I drink tea", kept=True)  # facts already exist: the old text still moves over
        s = S(about_me="I live in Pune.")
        with mock.patch.object(kn, "KEPT_MAX", 1):
            kn.migrate_about_me(s, self.k)
        self.assertEqual(s["about_me"], "I live in Pune.")  # full: kept, not lost

    def test_forget_matches_the_facts_own_words(self):
        self.k.add("They like tea", kept=True)
        self.k.add("Their sister is Priya", kept=True)
        self.assertEqual(self.k.forget("They like coffee"), [])  # shares only "they": nothing deleted
        self.assertEqual(self.k.forget("their sister"), ["Their sister is Priya"])


class LoadModes(unittest.TestCase):
    def test_speed_to_engine_values(self):
        self.assertEqual(loading("efficient"), {"big_model_policy": "on_demand", "ollama_keep_alive": "2m"})
        self.assertEqual(loading("auto"), {"big_model_policy": "on_demand", "ollama_keep_alive": "30m"})
        self.assertEqual(loading("full"), {"big_model_policy": "instant", "ollama_keep_alive": "30m"})

    def test_is_big(self):
        self.assertTrue(is_big("Waking up the big model"))
        self.assertFalse(is_big(""))


class Shortcut(unittest.TestCase):
    def test_readable_trigger(self):
        self.assertEqual(readable_trigger("Press <Alt><Super>space"), "Press Alt+Super+Space")
        self.assertEqual(readable_trigger(""), "")


if __name__ == "__main__":
    unittest.main()
