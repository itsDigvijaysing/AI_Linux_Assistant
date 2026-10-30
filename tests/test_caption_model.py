"""The pill caption's rules: markup, scrolling, sizes, motion and which caption is up."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.ui import caption_model as cm  # noqa: E402


class MarkupTests(unittest.TestCase):
    def test_settled_words_solid_rest_dimmed(self):
        self.assertEqual(cm.live_markup("open the files app", 3), 'open the files <span alpha="45%">app</span>')

    def test_all_settled_or_none(self):
        self.assertEqual(cm.live_markup("open files", 2), "open files")
        self.assertEqual(cm.live_markup("open files", 0, dim=0.6), '<span alpha="60%">open files</span>')

    def test_text_is_escaped(self):
        self.assertEqual(cm.live_markup("rock & <roll>", 9), "rock &amp; &lt;roll&gt;")

    def test_stable_is_clamped(self):
        self.assertEqual(cm.live_markup("a b", -4), '<span alpha="45%">a b</span>')


class GeometryTests(unittest.TestCase):
    def test_short_line_is_centred(self):
        self.assertEqual(cm.scroll_target(100, 300), 100)

    def test_long_line_keeps_its_end_in_view(self):
        self.assertEqual(cm.scroll_target(500, 300), -200)

    def test_edges_fade_only_where_words_run_past(self):
        self.assertEqual(cm.edge_fades(100, 100, 300), (0.0, 0.0))
        self.assertEqual(cm.edge_fades(-200, 500, 300), (1.0, 0.0))
        left, right = cm.edge_fades(-14, 300, 300, fade=28)
        self.assertAlmostEqual(left, 0.5)
        self.assertEqual(right, 0.0)

    def test_reply_width_between_live_line_and_max(self):
        self.assertEqual(cm.reply_width(10), cm.LIVE_W)
        self.assertEqual(cm.reply_width(5000), cm.REPLY_W)
        self.assertEqual(cm.reply_width(330.2), 331 + 2 * cm.PAD_X)

    def test_balanced_width_keeps_the_line_count(self):
        def lines_at(px):  # 600 px of text wrapped at px
            return -(-600 // px)

        width = cm.balanced_width(lines_at, 400, 200, step=1)
        self.assertEqual(lines_at(width), 2)
        self.assertEqual(width, 300)  # two even lines instead of 400 + 200

    def test_balanced_width_leaves_one_line_alone(self):
        self.assertEqual(cm.balanced_width(lambda px: 1, 400, 200), 400)


class MotionTests(unittest.TestCase):
    def test_approach_moves_toward_and_lands(self):
        x = cm.approach(0.0, 100.0, 0.11, 0.11)
        self.assertAlmostEqual(x, 100 * (1 - 1 / 2.718281828), places=3)
        self.assertEqual(cm.approach(99.9, 100.0, 0.01, 0.11), 100.0)

    def test_approach_is_frame_rate_independent(self):
        one = cm.approach(0.0, 100.0, 0.1, 0.11, snap=0)
        two = cm.approach(cm.approach(0.0, 100.0, 0.05, 0.11, snap=0), 100.0, 0.05, 0.11, snap=0)
        self.assertAlmostEqual(one, two)

    def test_no_motion_snaps(self):
        self.assertEqual(cm.approach(0.0, 5.0, 0.01, 0), 5.0)


class ModelTests(unittest.TestCase):
    def test_words_then_reply_then_collapse(self):
        m = cm.CaptionModel()
        self.assertTrue(m.live("open the", 1, False))
        self.assertEqual((m.mode, m.stable), ("live", 1))
        self.assertTrue(m.live("open the files", 3, True))
        self.assertEqual(m.stable, 3)
        self.assertFalse(m.state("thinking"))
        self.assertTrue(m.saying("Opening Files."))
        self.assertEqual((m.mode, m.text), ("reply", "Opening Files."))
        self.assertFalse(m.state("speaking"))
        self.assertTrue(m.state("followup"))
        self.assertEqual(m.mode, "hidden")

    def test_final_marks_every_word_settled(self):
        m = cm.CaptionModel()
        m.live("open the files", 0, True)
        self.assertEqual(m.stable, 3)

    def test_same_update_is_not_a_change(self):
        m = cm.CaptionModel()
        m.live("open  the", 1, False)
        self.assertFalse(m.live("open the", 1, False))
        m.saying("Done.")
        self.assertFalse(m.saying("Done."))

    def test_empty_update_clears_words_but_not_a_reply(self):
        m = cm.CaptionModel()
        m.live("open", 0, False)
        self.assertTrue(m.live("", 0, True))
        self.assertEqual(m.mode, "hidden")
        m.saying("Done.")
        self.assertFalse(m.live("", 0, True))
        self.assertEqual(m.mode, "reply")

    def test_listening_drops_an_old_reply_but_keeps_your_words(self):
        m = cm.CaptionModel()
        m.live("open", 0, False)
        self.assertFalse(m.state("listening"))
        m.saying("Done.")
        self.assertTrue(m.state("listening"))
        self.assertEqual(m.mode, "hidden")

    def test_hide_twice_is_one_change(self):
        m = cm.CaptionModel()
        self.assertFalse(m.hide())
        m.saying("Hi.")
        self.assertTrue(m.hide())
        self.assertFalse(m.state("idle"))


if __name__ == "__main__":
    unittest.main()
