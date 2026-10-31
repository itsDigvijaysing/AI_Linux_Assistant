"""Dictation: start and stop phrases, the light clean-up, and the engine typing each sentence (no model)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import dictation as D  # noqa: E402
from ai_linux.engine.quick import quick  # noqa: E402


class PhraseTests(unittest.TestCase):
    def test_start(self):
        self.assertEqual(D.start("Dictate."), ("polish", ""))
        self.assertEqual(D.start("dictate, hello there"), ("polish", "hello there"))
        self.assertEqual(D.start("Start dictation"), ("polish", ""))
        self.assertEqual(D.start("type what I say: see you at five"), ("exact", "see you at five"))
        self.assertEqual(D.start("Type exactly."), ("exact", ""))
        for text in ("what does dictate mean", "open the dictation app settings", "play some music", ""):
            self.assertIsNone(D.start(text), text)

    def test_start_as_people_say_it(self):
        for text in ("let's dictate", "Let's dictate.", "lets dictate now", "let's start dictating", "Can you dictate?",
                     "could you start dictation", "I want to dictate", "I'd like to dictate something", "I need to dictate",
                     "Um, let's dictate right now.", "Okay, dictate.", "turn on dictation", "Please take dictation."):
            self.assertEqual(D.start(text), ("polish", ""), text)
        self.assertEqual(D.start("Um dictate, hello there"), ("polish", "hello there"))

    def test_talking_about_dictation_does_not_start_it(self):
        for text in ("It works on battery but dictate is not working correctly",
                     "Actually when we dictate uh I want to make sure that it understands",
                     "Can you dictate this letter to John", "Dictate is not working", "dictate doesn't work",
                     "So dictate did not work at all", "Why can't I dictate", "I want to dictate a letter to John"):
            self.assertIsNone(D.start(text), text)

    def test_a_short_mention_gets_the_how_to_not_the_model(self):
        for text in ("Can you dictate this letter to John", "Dictate is not working", "How do I dictate?",
                     "Is dictation on?", "So dictation isn't working", "Why can't I dictate"):
            self.assertIn("Phoenix, dictate", quick(text, "Phoenix").say, text)  # the model said "I am ready to listen"
        for text in ("Remind me to dictate the letter at 5", "add dictate the memo to Todoist",
                     "what does dictate mean"):  # a request or a question for the tools and the model
            self.assertIsNone(quick(text, "Phoenix"), text)
        self.assertIsNone(quick("It works on battery but dictate is not working correctly and I want logs", "Phoenix"))
        self.assertIsNone(quick("play some music", "Phoenix"))

    def test_stopping_when_it_is_not_on(self):
        self.assertEqual(quick("Stop dictating.", "Phoenix").say, "Dictation isn't on.")

    def test_stop(self):
        for text in ("Stop dictated.", "Stop the dictation", "End dictation.", "finish dictating", "Done dictating.",
                     "I'm done dictating.", "I am done with the dictation"):
            self.assertEqual(D.split_stop(text), ("", True), text)
        for text in ("Stop dictation.", "stop dictating", "Please stop dictating!"):
            self.assertEqual(D.split_stop(text), ("", True), text)
        for text in ("stop", "Done.", "that's all", "stop the music when it ends", "I'm done with the report", "hi"):
            self.assertFalse(D.split_stop(text)[1], text)  # "done" or "that's all" can be part of the text

    def test_stop_at_the_end_of_a_sentence(self):
        self.assertEqual(D.split_stop("That is the plan. Stop dictating."), ("That is the plan.", True))
        self.assertEqual(D.split_stop("that is the plan, Phoenix, stop dictating", "phoenix"),
                         ("that is the plan", True))
        self.assertEqual(D.split_stop("we must stop dictating rules to them"), ("we must stop dictating rules to them",
                                                                                False))

    def test_stop_as_people_say_it(self):
        for text in ("Stop dictating?", "Stop dictating,", "Stop dictating now.", "Stop dictating, please.",
                     "stop dictating thanks", "Stop dictating, thank you.", "Stop dictating, Phoenix.",
                     "Phoenix, stop dictating.", "Okay Phoenix, stop dictating."):
            self.assertEqual(D.split_stop(text, "phoenix"), ("", True), text)
        self.assertEqual(D.split_stop("The plan. Phoenix, stop dictating", "phoenix"), ("The plan.", True))
        self.assertEqual(D.split_stop("Okay stop dictating."), ("Okay", True))  # no name: the word may be text

    def test_a_name_that_ends_a_sentence_is_text(self):
        self.assertEqual(D.split_stop("We flew to Phoenix. Stop dictating.", "phoenix"), ("We flew to Phoenix.", True))
        self.assertEqual(D.split_stop("Okay Phoenix. Stop dictating.", "phoenix"), ("", True))
        self.assertEqual(D.split_stop("The plan. Phoenix. Stop dictating.", "phoenix"), ("The plan.", True))


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

    def test_a_question_inside_the_text_stays_a_question(self):
        said = "Is the shop open? I need milk and some bread."
        self.assertEqual(D.pick(said, "Yes, the shop is open. I need milk and some bread."), said)
        fixed = "Is the shop open? I need milk and bread."
        self.assertEqual(D.pick(said, fixed), fixed)

    def test_a_long_text_may_gain_a_question_mark(self):
        said = "where are you going I am here and I will wait for you at the station"
        fixed = "Where are you going? I am here and I will wait for you at the station."
        self.assertEqual(D.pick(said, fixed), fixed)

    def test_a_short_text_gaining_a_question_is_the_model_asking_back(self):
        said = "Write the report for Monday."
        self.assertEqual(D.pick(said, "What should the report for Monday say?"), said)

    def test_a_question_mark_on_the_same_words_is_a_fix(self):
        said = "Can you write a poem about the ocean."
        self.assertEqual(D.pick(said, "Can you write a poem about the ocean?"), "Can you write a poem about the ocean?")

    def test_the_model_never_adds_a_dash(self):
        self.assertEqual(D.pick("make a paragraph or maybe a schedule to switch those",
                                "Make a paragraph\u2014or maybe a schedule\u2014to switch those."),
                         "Make a paragraph, or maybe a schedule, to switch those.")

    def test_line_breaks_said_out_loud_stay(self):
        said = "Dear Sam,\nSee you soon."
        self.assertEqual(D.pick(said, "Dear Sam, see you soon."), said)
        self.assertEqual(D.pick(said, "Dear Sam,\nSee you soon!"), "Dear Sam,\nSee you soon!")
        self.assertEqual(D.pick("Hello there\n\n", "Hello there."), "Hello there.\n\n")


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
