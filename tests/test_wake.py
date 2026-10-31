"""Wake matcher and the name test: pure logic, runs anywhere."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import wake  # noqa: E402

NAMES = ("phoenix", "doraemon", "orion", "computer", "hey buddy")


class Matcher(unittest.TestCase):
    def test_every_test_sentence_contains_a_findable_name(self):
        for name in NAMES:
            for sentence in wake.phrases(name):
                self.assertTrue(wake.heard(sentence, name), (name, sentence))
            self.assertFalse(wake.heard(wake.NEGATIVE, name), name)

    def test_a_name_spelled_as_it_sounds(self):
        for said in ("Fenix.", "Phenix", "Feenix?"):  # 2026-10-31: a plain "Phoenix" came back as "Fenix." and was ignored
            self.assertEqual(wake.strip_wake(said, "phoenix"), "", said)
            self.assertTrue(wake.is_name(said, "phoenix"), said)
        self.assertEqual(wake.strip_wake("Fenix, what time is it?", "phoenix"), "what time is it?")

    def test_a_different_name_is_not_the_name(self):
        for said in ("Felix is here", "Finish it", "Phone it in"):
            self.assertIsNone(wake.strip_wake(said, "phoenix"), said)
        self.assertFalse(wake.is_name("Felix", "phoenix"))

    def test_split_name(self):
        self.assertEqual(wake.strip_wake("Dora Emon, open my files", "doraemon"), "open my files")
        self.assertIsNone(wake.strip_wake("we bought a new computer case", "computer"))  # 5th word, joined is longer

    def test_is_name(self):
        for text, name in (("Phoenix.", "phoenix"), ("Phenix", "phoenix"), ("Hey, buddy!", "hey buddy")):
            self.assertTrue(wake.is_name(text, name), text)
        for text, name in (("We flew to Phoenix.", "phoenix"), ("", "phoenix"), ("Phoenix", ""), ("buddy", "hey buddy")):
            self.assertFalse(wake.is_name(text, name), text)

    def test_empty_name_passes_text_through(self):
        self.assertEqual(wake.strip_wake("open files", ""), "open files")
        self.assertFalse(wake.heard("open files", ""))

    def test_warnings(self):
        self.assertEqual(wake.warnings(""), ["Type a name first."])
        self.assertTrue(wake.warnings("Max"))
        self.assertTrue(wake.warnings("one two three"))
        self.assertEqual(wake.warnings("Phoenix"), [])


class NameTestRun(unittest.TestCase):
    def run_with(self, heard_texts):
        t = wake.NameTest("Phoenix")
        for text in heard_texts:
            self.assertIsNotNone(t.current)
            t.record(text)
        self.assertTrue(t.done)
        self.assertIsNone(t.current)
        return t.verdict()[0]

    def test_all_heard(self):
        self.assertEqual(self.run_with(wake.phrases("phoenix") + ["open my files and play some music"]), "good")

    def test_some_missed(self):
        heard = wake.phrases("phoenix")[:3] + ["hello fee nicks", "", "open my files"]
        self.assertEqual(self.run_with(heard), "fair")

    def test_mostly_missed(self):
        self.assertEqual(self.run_with(["hello", "", "wake up", "", "", ""]), "poor")

    def test_false_alarm_is_poor(self):
        self.assertEqual(self.run_with(wake.phrases("phoenix") + ["phoenix open my files"]), "poor")

    def test_negative_comes_last(self):
        t = wake.NameTest("Phoenix")
        self.assertEqual(t.sentences[-1], wake.NEGATIVE)
        self.assertFalse(t.on_negative)


if __name__ == "__main__":
    unittest.main()
