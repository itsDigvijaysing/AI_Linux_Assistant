"""Unit tests for the pure bar animation model (no GTK needed)."""

from __future__ import annotations

import math
import sys
import unittest
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.ui import bar_model as bm  # noqa: E402
from ai_linux.ui.bar_model import (  # noqa: E402
    BLEND_S, COLORS, GEOMETRY, MUTE_BLEND_S, N_BARS, SHAKE_S, TAPER, BarAnimator, Frame, level_from_rms,
)

DT = 1 / 60


def run(anim: BarAnimator, t0: float, seconds: float, level=0.0) -> list[Frame]:
    """Step at 60 fps; ``level`` may be a constant or a function of time."""
    frames, t = [], t0
    while t < t0 + seconds:
        lv = level(t) if callable(level) else level
        frames.append(anim.step(t, lv))
        t += DT
    return frames


def settled_anim(state: str, reduced: bool = False, t0: float = 100.0) -> tuple[BarAnimator, float]:
    """An animator that entered ``state`` at t0 and has finished blending."""
    anim = BarAnimator(reduced_motion=reduced)
    anim.step(t0 - 1.0)
    anim.set_state(state, t0)
    return anim, t0 + max(BLEND_S, MUTE_BLEND_S) + 0.01


class HeightsPerState(unittest.TestCase):
    def assert_within(self, frames, lo, hi, msg):
        for f in frames:
            self.assertEqual(len(f.heights), N_BARS)
            for h, e in zip(f.heights, TAPER):
                self.assertGreaterEqual(h, lo * e - 1e-9, msg)
                self.assertLessEqual(h, hi * e + 1e-9, msg)

    def test_idle_breathes_between_030_and_038(self):
        anim, t = settled_anim("followup")
        anim.set_state("idle", t)
        frames = run(anim, t + BLEND_S + 0.01, 4.0)
        self.assert_within(frames, 0.30, 0.38, "idle")
        centre = [f.heights[bm.CENTER] for f in frames]
        self.assertGreater(max(centre) - min(centre), 0.05, "idle should visibly breathe")
        self.assertEqual(frames[-1].color, COLORS["idle"])

    def test_followup_like_idle_with_progress(self):
        anim, t = settled_anim("followup")
        frames = [anim.step(t + i * DT, 0.0, progress=0.5) for i in range(120)]
        self.assert_within(frames, 0.30, 0.38, "followup")
        self.assertTrue(all(f.progress == 0.5 for f in frames))
        self.assertEqual(frames[0].color, COLORS["followup"])

    def test_progress_only_in_followup(self):
        anim, t = settled_anim("listening")
        self.assertIsNone(anim.step(t, 0.2, progress=0.7).progress)

    def test_listening_follows_level(self):
        anim, t = settled_anim("listening")
        quiet = run(anim, t, 1.0, 0.0)
        self.assert_within(quiet, 0.12, 0.12 + 0.01, "listening quiet rests at 0.12")
        loud = run(anim, t + 1.0, 1.0, 1.0)
        self.assert_within(loud, 0.12, 1.0, "listening loud")
        for h, e in zip(loud[-1].heights, TAPER):
            self.assertAlmostEqual(h, e, delta=0.01)

    def test_listening_centre_leads_edges(self):
        anim, t = settled_anim("listening")
        run(anim, t, 0.5, 0.0)
        frames = run(anim, t + 0.5, 0.06, 1.0)  # a fresh onset reaches the centre bar first
        f = frames[-1]
        self.assertGreater(f.heights[bm.CENTER] / TAPER[bm.CENTER], f.heights[0] / TAPER[0])

    def test_thinking_and_loading_sweep(self):
        for state in ("thinking", "loading"):
            anim, t = settled_anim(state)
            frames = run(anim, t, 2.5)
            self.assert_within(frames, 0.35, 0.90, state)
            for f in frames:
                self.assertTrue(all(0.55 - 1e-9 <= o <= 1.0 + 1e-9 for o in f.opacities))
            peaks = {max(range(N_BARS), key=lambda i: f.heights[i] / TAPER[i]) for f in frames}
            self.assertGreaterEqual(len(peaks), 5, f"{state} bump should travel across the bars")

    def test_speaking_scales_with_tts_level(self):
        anim, t = settled_anim("speaking")
        frames = run(anim, t, 1.5, 0.8)
        self.assert_within(frames, 0.15 * 0.88, 1.0, "speaking")
        quiet_anim, t2 = settled_anim("speaking")
        quiet = run(quiet_anim, t2, 1.0, 0.0)
        self.assertLess(max(f.heights[bm.CENTER] for f in quiet), max(f.heights[bm.CENTER] for f in frames))
        self.assertEqual(frames[0].color, COLORS["speaking"])

    def test_fire_only_on_the_assistants_turn(self):
        for dark, centre, edge in ((False, "#f7a600", "#e2440a"), (True, "#fecc1a", "#fa4801")):
            colors = bm.fire_colors(dark)
            self.assertEqual(len(colors), N_BARS)
            self.assertEqual(colors, colors[::-1])
            self.assertEqual((colors[bm.CENTER], colors[0]), (centre, edge))
        for state in ("thinking", "speaking"):
            anim, t = settled_anim(state)
            self.assertTrue(anim.step(t).fire, state)
        for state in ("idle", "listening", "followup", "muted", "error", "off"):
            anim, t = settled_anim(state)
            self.assertFalse(anim.step(t).fire, state)

    def test_muted_flat_dots(self):
        anim, t = settled_anim("muted")
        f = anim.step(t + 0.1)
        self.assertTrue(all(h == 0 for h in f.heights))
        self.assertEqual(f.color, COLORS["muted"])

    def test_error_flat_and_red(self):
        anim, t = settled_anim("error")
        f = anim.step(t + SHAKE_S + 0.1)
        self.assert_within([f], 0.30, 0.30, "error")
        self.assertEqual(f.color, COLORS["error"])
        self.assertEqual(f.x_offset, 0.0)

    def test_error_shakes_then_stops(self):
        anim = BarAnimator()
        anim.step(0.0)
        anim.set_state("error", 1.0)
        offsets = [anim.step(1.0 + i * DT).x_offset for i in range(int(SHAKE_S / DT))]
        self.assertGreater(max(abs(o) for o in offsets), 1.0)
        self.assertLessEqual(max(abs(o) for o in offsets), 4.0)
        self.assertEqual(anim.step(1.0 + SHAKE_S + 0.01).x_offset, 0.0)

    def test_off_dimmed(self):
        anim, t = settled_anim("off")
        f = anim.step(t)
        self.assertEqual(f.color, COLORS["off"])
        self.assertTrue(all(o == 0.6 for o in f.opacities))

    def test_unknown_state_falls_back_to_off(self):
        anim, t = settled_anim("bogus")
        self.assertEqual(anim.step(t).color, COLORS["off"])


class Blending(unittest.TestCase):
    def test_blend_reaches_target_at_blend_s(self):
        anim = BarAnimator()
        anim.step(0.0)
        start = anim.step(1.0).heights
        anim.set_state("muted", 1.0)
        mid = anim.step(1.0 + MUTE_BLEND_S / 2).heights
        end = anim.step(1.0 + MUTE_BLEND_S).heights
        self.assertTrue(all(0 < m < s for m, s in zip(mid, start)))
        self.assertTrue(all(e == 0 for e in end))

    def test_blend_is_ease_out(self):
        anim = BarAnimator()
        anim.step(0.0)
        s = anim.step(1.0).heights[bm.CENTER]
        anim.set_state("muted", 1.0)
        q = anim.step(1.0 + MUTE_BLEND_S / 4).heights[bm.CENTER]
        self.assertLess(q, s * (1 - 0.25), "ease-out covers more than a linear quarter early on")

    def test_normal_blend_duration(self):
        anim = BarAnimator()
        anim.step(0.0)
        anim.set_state("error", 1.0)
        anim.step(1.0 + BLEND_S * 0.5)
        self.assertIsNotNone(anim._blend_from)
        anim.step(1.0 + BLEND_S + 1e-6)
        self.assertIsNone(anim._blend_from)

    def test_same_state_does_not_restart_blend(self):
        anim = BarAnimator()
        anim.step(0.0)
        anim.set_state("muted", 1.0)
        anim.step(2.0)
        anim.set_state("muted", 2.0)
        self.assertIsNone(anim._blend_from)

    def test_accepts_str_enum_states(self):
        class S(str, Enum):
            LISTENING = "listening"

        anim = BarAnimator()
        anim.set_state(S.LISTENING, 1.0)
        self.assertEqual(anim.state, "listening")
        self.assertIs(type(anim.state), str)
        self.assertEqual(anim.fps(), 60)


class SettleAndFps(unittest.TestCase):
    def test_fps_per_state(self):
        expected = {"off": 0, "idle": 20, "followup": 20, "listening": 60, "thinking": 30,
                    "loading": 20, "speaking": 60, "muted": 0, "error": 0}
        anim = BarAnimator()
        for state, fps in expected.items():
            anim.set_state(state, 0.0)
            self.assertEqual(anim.fps(), fps, state)

    def test_reduced_motion_fps(self):
        anim = BarAnimator(reduced_motion=True)
        for state in ("idle", "followup", "thinking", "loading"):
            anim.set_state(state, 0.0)
            self.assertEqual(anim.fps(), 0, state)
        for state in ("listening", "speaking"):
            anim.set_state(state, 0.0)
            self.assertEqual(anim.fps(), 60, state)

    def test_muted_settles_after_blend(self):
        anim = BarAnimator()
        anim.step(0.0)
        anim.set_state("muted", 1.0)
        self.assertFalse(anim.is_settled(1.0 + MUTE_BLEND_S - 0.01))
        self.assertTrue(anim.is_settled(1.0 + MUTE_BLEND_S + 0.01))

    def test_error_settles_after_shake(self):
        anim = BarAnimator()
        anim.set_state("error", 1.0)
        self.assertFalse(anim.is_settled(1.0 + SHAKE_S - 0.01))
        self.assertTrue(anim.is_settled(1.0 + SHAKE_S + 0.01))

    def test_error_reduced_motion_settles_after_blend(self):
        anim = BarAnimator(reduced_motion=True)
        anim.set_state("error", 1.0)
        self.assertFalse(anim.is_settled(1.0 + BLEND_S - 0.01))
        self.assertTrue(anim.is_settled(1.0 + BLEND_S + 0.01))

    def test_animated_states_never_settle(self):
        for state in ("idle", "followup", "thinking", "loading", "speaking"):
            anim, t = settled_anim(state)
            self.assertFalse(anim.is_settled(t + 5, 0.0), state)

    def test_listening_settles_only_when_quiet(self):
        anim, t = settled_anim("listening")
        self.assertFalse(anim.is_settled(t), "no level given keeps listening animated")
        run(anim, t, 1.0, 0.9)
        self.assertFalse(anim.is_settled(t + 1.0, 0.0), "still releasing")
        frames = run(anim, t + 1.0, 2.0, 0.0)
        self.assertTrue(anim.is_settled(t + 3.0, 0.0))
        self.assertFalse(anim.is_settled(t + 3.0, 0.3), "a rising level wakes it")
        self.assertFalse(anim.is_settled(t + 3.0, None))
        rest = frames[-1].heights
        self.assertAlmostEqual(rest[bm.CENTER], 0.12, delta=0.002)

    def test_reduced_motion_idle_static(self):
        anim, t = settled_anim("idle", reduced=True)
        frames = run(anim, t, 2.0)
        self.assertTrue(all(f.heights == frames[0].heights for f in frames))
        for h, e in zip(frames[0].heights, TAPER):
            self.assertAlmostEqual(h, 0.34 * e)
        self.assertTrue(anim.is_settled(t))

    def test_reduced_motion_thinking_static(self):
        anim, t = settled_anim("thinking", reduced=True)
        frames = run(anim, t, 1.0)
        self.assertTrue(all(f.heights == frames[0].heights for f in frames))
        self.assertTrue(all(o == 1.0 for o in frames[0].opacities))

    def test_reduced_motion_speaking_uniform(self):
        anim, t = settled_anim("speaking", reduced=True)
        f = run(anim, t, 1.0, 0.5)[-1]
        ratios = [h / e for h, e in zip(f.heights, TAPER)]
        self.assertAlmostEqual(max(ratios), min(ratios))


class LevelFromRms(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(level_from_rms(0.0), 0.0)
        self.assertEqual(level_from_rms(1.0), 1.0)
        self.assertEqual(level_from_rms(10 ** (-52 / 20)), 0.0)
        self.assertAlmostEqual(level_from_rms(10 ** (-14 / 20)), 1.0)

    def test_monotonic_and_perceptual(self):
        values = [level_from_rms(10 ** (db / 20)) for db in range(-60, 1)]
        self.assertEqual(values, sorted(values))
        mid = level_from_rms(10 ** (-33 / 20))  # halfway in dB
        self.assertGreater(mid, 0.5, "the 0.7 power lifts quiet speech")
        self.assertAlmostEqual(mid, 0.5 ** 0.7, places=3)

    def test_custom_range(self):
        self.assertEqual(level_from_rms(10 ** (-30 / 20), floor_db=-30, ceil_db=-10), 0.0)


class GeometryAndHelpers(unittest.TestCase):
    def test_geometry_widths(self):
        self.assertEqual(GEOMETRY["hero"].width, 11 * 10 + 10 * 8)
        self.assertEqual(GEOMETRY["pill"].width, 11 * 4 + 10 * 3)
        self.assertLessEqual(GEOMETRY["tray"].width, 32)
        for name, g in GEOMETRY.items():
            self.assertGreaterEqual(g.min_h, g.bar_w, f"{name}: a quiet bar must be at least a dot")
            self.assertGreater(g.max_h, g.min_h, name)

    def test_taper_symmetric_peak_centre(self):
        self.assertEqual(list(TAPER), list(reversed(TAPER)))
        self.assertEqual(max(TAPER), TAPER[bm.CENTER])

    def test_colors_cover_states(self):
        for state in bm.FPS:
            self.assertIn(state, COLORS)
        for c in COLORS.values():
            r, g, b = bm.hex_to_rgb(c)
            self.assertTrue(all(0 <= v <= 1 for v in (r, g, b)))
        self.assertEqual(bm.hex_to_rgb("#ff0000"), (1.0, 0.0, 0.0))

    def test_level_follower_is_frame_rate_independent(self):
        a, b = bm.LevelFollower(0.04, 0.16), bm.LevelFollower(0.04, 0.16)
        for _ in range(60):
            a.update(1.0, 1 / 60)
        for _ in range(144):
            b.update(1.0, 1 / 144)
        self.assertAlmostEqual(a.value, b.value, places=6)

    def test_ease_out_cubic(self):
        self.assertEqual(bm.ease_out_cubic(0), 0)
        self.assertEqual(bm.ease_out_cubic(1), 1)
        self.assertEqual(bm.ease_out_cubic(2), 1)
        self.assertTrue(math.isclose(bm.ease_out_cubic(0.5), 0.875))


if __name__ == "__main__":
    unittest.main()
