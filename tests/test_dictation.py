"""Dictation: start and stop phrases, the light clean-up, and the engine typing each sentence (no model)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import dictation as D  # noqa: E402


class PhraseTests(unittest.TestCase):
    def test_start(self):
        self.assertEqual(D.start("Dictate."), "")
        self.assertEqual(D.start("dictate, hello there"), "hello there")
        self.assertEqual(D.start("Start dictation"), "")
        self.assertEqual(D.start("type what I say: see you at five"), "see you at five")
        for text in ("what does dictate mean", "open the dictation app settings", "play some music", ""):
            self.assertIsNone(D.start(text), text)

    def test_stop(self):
        for text in ("Stop dictation.", "stop", "Done.", "that's all", "end dictation", "stop typing"):
            self.assertTrue(D.is_stop(text), text)
        for text in ("stop the music when it ends", "I'm done with the report", "hello"):
            self.assertFalse(D.is_stop(text), text)


class CleanTests(unittest.TestCase):
    def test_fillers_go_and_words_stay(self):
        self.assertEqual(D.clean("Um, I think, uh, we should meet."), "I think, we should meet.")
        self.assertEqual(D.clean("so hmm let's go"), "So let's go")

    def test_spoken_punctuation_and_lines(self):
        self.assertEqual(D.clean("Dear Sam comma new line thanks for the update full stop"),
                         "Dear Sam,\nThanks for the update.")
        self.assertEqual(D.clean("Is it ready question mark New paragraph. Yes."), "Is it ready?\n\nYes.")
        self.assertEqual(D.clean("the trial period ends soon"), "The trial period ends soon")

    def test_nothing_left(self):
        self.assertEqual(D.clean("Um."), "")
        self.assertEqual(D.clean("   "), "")


if __name__ == "__main__":
    unittest.main()
