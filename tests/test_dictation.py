"""Dictation: start and stop phrases, the light clean-up, and the engine typing each sentence (no model)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import dictation as D  # noqa: E402


class PhraseTests(unittest.TestCase):
    def test_start(self):
        self.assertEqual(D.start("Dictate."), ("polish", ""))
        self.assertEqual(D.start("dictate, hello there"), ("polish", "hello there"))
        self.assertEqual(D.start("Start dictation"), ("polish", ""))
        self.assertEqual(D.start("type what I say: see you at five"), ("exact", "see you at five"))
        self.assertEqual(D.start("Type exactly."), ("exact", ""))
        for text in ("what does dictate mean", "open the dictation app settings", "play some music", ""):
            self.assertIsNone(D.start(text), text)

    def test_stop(self):
        for text in ("Stop dictation.", "stop", "Done.", "that's all", "end dictation", "stop typing"):
            self.assertTrue(D.is_stop(text), text)
        for text in ("stop the music when it ends", "I'm done with the report", "hello"):
            self.assertFalse(D.is_stop(text), text)


class PickTests(unittest.TestCase):
    """The big model's rewrite is used only when it still looks like the same sentence."""

    def test_a_fixed_sentence_is_used(self):
        self.assertEqual(D.pick("I has went to the shop yesterday and buyed milk.",
                                "I went to the shop yesterday and bought milk."),
                         "I went to the shop yesterday and bought milk.")
        self.assertEqual(D.pick("hello", '"Hello."'), "Hello.")  # quotes around the reply go

    def test_answers_and_additions_fall_back_to_what_was_said(self):
        said = "What time does the meeting start?"
        self.assertEqual(D.pick(said, "The meeting starts at 10 AM."), said)  # it answered instead
        self.assertEqual(D.pick("Thanks for the update.", "Thanks for the update. I will review the document, "
                                "share my notes with the team and get back to you by Friday."),
                         "Thanks for the update.")  # it added things
        self.assertEqual(D.pick("Please send the report today.", ""), "Please send the report today.")
        self.assertEqual(D.pick("See you soon.", "<think>x</think>See you soon!"), "See you soon!")


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
