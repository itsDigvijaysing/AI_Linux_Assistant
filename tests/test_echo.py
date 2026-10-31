"""Cancelling our own voice inside the engine: when it runs, what it is fed, and that it can't deafen the mic."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import echo  # noqa: E402


class FakeAec:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def process(self, near, far):
        if self.fail:
            raise RuntimeError("broken")
        self.calls.append((near.copy(), far.copy()))
        return near * 0.5


def frame(value=0.25):
    return np.full(512, value, np.float32)


class WhenItRuns(unittest.TestCase):
    def test_a_silent_phoenix_leaves_the_mic_untouched(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        np.testing.assert_array_equal(c.clean(frame()), frame())
        self.assertEqual(aec.calls, [])

    def test_while_speaking_every_frame_stays_512_samples(self):
        c = echo.Canceller(FakeAec())
        c.feed(np.zeros(16000, np.float32), 16000)
        self.assertEqual({len(c.clean(frame())) for _ in range(40)}, {512})

    def test_while_speaking_the_output_is_the_cancelled_signal(self):
        c = echo.Canceller(FakeAec())
        c.feed(np.zeros(16000, np.float32), 16000)
        c.clean(frame())
        self.assertAlmostEqual(float(c.clean(frame())[-1]), 0.125)

    def test_after_the_sound_ends_it_goes_back_to_the_untouched_mic(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        c.feed(np.zeros(1600, np.float32), 16000)  # 0.1 s
        for _ in range(int((0.1 + echo.TAIL_S) * 16000 / 512) + 3):
            c.clean(frame())
        used = len(aec.calls)
        np.testing.assert_array_equal(c.clean(frame()), frame())
        self.assertEqual(len(aec.calls), used)

    def test_a_stopped_sound_still_cancels_what_the_room_carries(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        c.feed(np.ones(16000, np.float32), 16000)
        c.clean(frame())
        c.stop()
        used = len(aec.calls)
        self.assertAlmostEqual(float(c.clean(frame())[-1]), 0.125)
        self.assertGreater(len(aec.calls), used)
        self.assertEqual(float(np.abs(aec.calls[-1][1]).max()), 0.0)  # what was not played is not the reference

    def test_a_closed_mic_forgets_the_sound(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        c.feed(np.ones(16000, np.float32), 16000)
        c.reset()
        np.testing.assert_array_equal(c.clean(frame()), frame())
        self.assertEqual(aec.calls, [])


class WhatItIsFed(unittest.TestCase):
    def test_mic_and_sound_go_in_step_in_10_ms_blocks(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        far = np.arange(16000, dtype=np.float32) / 16000
        c.feed(far, 16000)
        for _ in range(10):
            c.clean(frame())
        self.assertEqual({(len(n), len(f)) for n, f in aec.calls}, {(160, 160)})
        np.testing.assert_allclose(np.concatenate([f for _, f in aec.calls]), far[:len(aec.calls) * 160], atol=1e-6)

    def test_no_mic_sample_is_lost_or_doubled_between_frames(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        c.feed(np.zeros(16000, np.float32), 16000)
        said = np.arange(512 * 10, dtype=np.float32)
        for i in range(10):
            c.clean(said[i * 512:(i + 1) * 512])
        got = np.concatenate([n for n, _ in aec.calls])
        np.testing.assert_array_equal(got, said[:len(got)])

    def test_the_voice_is_brought_to_16_khz(self):
        c = echo.Canceller(FakeAec())
        c.feed(np.zeros(44100, np.float32), 44100)
        self.assertEqual(c.pending(), 16000)

    def test_a_cue_over_a_sentence_is_mixed_not_replaced(self):
        aec = FakeAec()
        c = echo.Canceller(aec)
        c.feed(np.full(16000, 0.1, np.float32), 16000)
        c.feed(np.full(1600, 0.2, np.float32), 16000)
        self.assertEqual(c.pending(), 16000)
        c.clean(frame())
        self.assertAlmostEqual(float(aec.calls[0][1][0]), 0.3, places=5)


class FailsSafe(unittest.TestCase):
    def test_a_broken_canceller_gives_back_the_plain_mic(self):
        c = echo.Canceller(FakeAec(fail=True))
        c.feed(np.zeros(16000, np.float32), 16000)
        np.testing.assert_array_equal(c.clean(frame()), frame())
        self.assertFalse(c.ok)

    def test_a_missing_library_means_no_canceller(self):
        with mock.patch.dict(sys.modules, {"pywebrtc_audio": None}):
            self.assertIsNone(echo.load())


@unittest.skipIf(importlib.util.find_spec("pywebrtc_audio") is None, "needs the engine's pywebrtc-audio")
class TheRealOne(unittest.TestCase):
    def test_it_removes_a_delayed_copy_of_the_sound(self):
        rng = np.random.default_rng(1)
        far = (rng.standard_normal(16000 * 6) * 0.1).astype(np.float32)
        near = np.concatenate([np.zeros(1600, np.float32), far[:-1600] * 0.5])  # 100 ms later, quieter
        c = echo.load()
        c.feed(far, 16000)
        out = [c.clean(near[i:i + 512]) for i in range(0, len(near) - 512, 512)]
        tail = np.concatenate(out[-60:])
        before = near[len(near) - len(tail):]
        self.assertLess(np.sqrt(np.mean(tail ** 2)), 0.1 * np.sqrt(np.mean(before ** 2)))  # at least 20 dB


if __name__ == "__main__":
    unittest.main()
