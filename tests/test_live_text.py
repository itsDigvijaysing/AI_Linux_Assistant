"""Words while you speak: settled words, pacing, the engine's wake gating, and the GPU encoder's padding."""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if importlib.util.find_spec("numpy") is None:
    raise unittest.SkipTest("numpy is not installed")
import numpy as np  # noqa: E402

from ai_linux.engine import live_text as lt  # noqa: E402
from ai_linux.engine.asr.ov_encoder import pad_frames  # noqa: E402


class SettledTests(unittest.TestCase):
    def test_normalize_ignores_case_and_punctuation(self):
        self.assertEqual(lt.normalize("Five."), "five")
        self.assertEqual(lt.normalize("don't,"), "don't")

    def test_common_prefix(self):
        self.assertEqual(lt.settled(["open", "the", "file"], ["Open", "the", "files", "app"]), 2)
        self.assertEqual(lt.settled([], ["open"]), 0)


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


class LiveTextTests(unittest.TestCase):
    def make(self, transcribe):
        got = []
        live = lt.LiveText(transcribe, threading.Lock(), lambda utt, text: got.append((utt, text)),
                           every=0.0, min_s=0.5, rate=100)
        return live, got

    def test_nothing_due_before_begin_or_while_short(self):
        live, _ = self.make(lambda a: "x")
        self.assertFalse(live.due(1000))
        live.begin(1)
        self.assertFalse(live.due(10))
        self.assertTrue(live.due(50))

    def test_update_reaches_the_current_utterance(self):
        live, got = self.make(lambda a: f" {len(a)} samples ")
        live.begin(3)
        self.assertTrue(live.offer(3, np.zeros(60, np.float32)))
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual(got, [(3, "60 samples")])
        self.assertTrue(wait_for(lambda: live.due(60)))  # busy clears after the decode

    def test_one_decode_at_a_time(self):
        gate = threading.Event()
        live, got = self.make(lambda a: gate.wait(2) and "done")
        live.begin(1)
        self.assertTrue(live.offer(1, np.zeros(60, np.float32)))
        self.assertFalse(live.due(60))
        self.assertFalse(live.offer(1, np.zeros(60, np.float32)))
        gate.set()
        self.assertTrue(wait_for(lambda: got))

    def test_stale_update_is_dropped(self):
        gate = threading.Event()
        live, got = self.make(lambda a: gate.wait(2) and "late")
        live.begin(1)
        live.offer(1, np.zeros(60, np.float32))
        time.sleep(0.05)
        live.end()  # the final decode takes over
        gate.set()
        time.sleep(0.1)
        self.assertEqual(got, [])
        self.assertFalse(live.offer(1, np.zeros(60, np.float32)))

    def test_failing_decode_is_silent(self):
        def boom(_a):
            raise RuntimeError("no")

        live, got = self.make(boom)
        live.begin(1)
        live.offer(1, np.zeros(60, np.float32))
        self.assertTrue(wait_for(lambda: live.due(60)))
        self.assertEqual(got, [])


class PadTests(unittest.TestCase):
    def test_pads_to_whole_buckets_and_keeps_length(self):
        feats = np.ones((1, 128, 250), np.float32)
        padded, t = pad_frames(feats, 100)
        self.assertEqual((padded.shape, t), ((1, 128, 300), 250))
        self.assertEqual(float(padded[:, :, 250:].sum()), 0.0)

    def test_exact_length_is_untouched(self):
        feats = np.ones((1, 128, 200), np.float32)
        padded, t = pad_frames(feats, 100)
        self.assertIs(padded, feats)
        self.assertEqual(t, 200)


@unittest.skipUnless(importlib.util.find_spec("onnxruntime"), "engine venv only")
class BucketEncoderTests(unittest.TestCase):
    def test_output_is_trimmed_to_the_true_length(self):
        from ai_linux.engine.asr import tdt_asr

        class Session:
            bucket = 100

            def run(self, names, feed):
                self.feed = feed
                t = feed["audio_signal"].shape[2] // 8
                return [np.ones((1, 1024, t), np.float32), np.array([int(feed["length"][0]) // 8])]

        model = tdt_asr._OnnxTDTModel.__new__(tdt_asr._OnnxTDTModel)
        model.encoder, model.encoder_in_names = Session(), ["audio_signal", "length"]
        out = model.run_encoder(np.ones((1, 128, 250), np.float32))
        self.assertEqual(model.encoder.feed["audio_signal"].shape, (1, 128, 300))
        self.assertEqual(int(model.encoder.feed["length"][0]), 250)
        self.assertEqual(out.shape, (1, 1024, 31))


class EngineLiveTests(unittest.TestCase):
    def make(self, settings=None):
        from ai_linux.engine.service import Engine

        events = []
        e = Engine(events.append, settings or {"listen_mode": "wake"})
        e._begin_utterance()
        return e, events

    @staticmethod
    def partials(events):
        return [(ev["text"], ev["stable"], ev["final"]) for ev in events if ev.get("ev") == "partial"]

    def test_talk_without_the_name_is_never_shown(self):
        e, events = self.make()
        e._on_live(e._utt, "what a nice day")
        self.assertEqual(self.partials(events), [])
        self.assertNotIn({"ev": "state", "state": "listening", "detail": ""}, events)

    def test_the_name_lights_up_and_shows_the_rest(self):
        e, events = self.make()
        e._on_live(e._utt, "Phoenix, open the")
        e._on_live(e._utt, "Phoenix, open the files")
        self.assertIn({"ev": "state", "state": "listening", "detail": ""}, events)
        self.assertEqual(self.partials(events), [("open the", 0, False), ("open the files", 2, False)])

    def test_old_utterance_and_own_voice_are_ignored(self):
        e, events = self.make({"listen_mode": "always"})
        e._on_live(e._utt - 1, "open files")
        e.speaking = True
        e._on_live(e._utt, "open files")
        e.speaking = False
        e._end_utterance()
        e._on_live(e._utt, "open files")
        self.assertEqual(self.partials(events), [])

    def test_always_on_with_the_talk_filter_waits_for_the_sentence(self):
        e, events = self.make({"listen_mode": "always", "addressed_check": True})
        e._on_live(e._utt, "and then the match went")
        self.assertEqual(self.partials(events), [])
        e.armed_until = time.monotonic() + 5  # asked by hotkey: shown
        e._on_live(e._utt, "open the files")
        self.assertEqual(self.partials(events), [("open the files", 0, False)])

    def test_rejected_sentence_clears_what_was_shown(self):
        e, events = self.make()
        e.asr = mock.Mock(transcribe=mock.Mock(return_value="what a day"))
        e._on_live(e._utt, "Phoenix open")
        e._end_utterance()
        e._one("audio", object(), 0.0)
        self.assertEqual(self.partials(events)[-1], ("", 0, True))

    def test_heard_sentence_is_sent_settled_before_thinking(self):
        e, events = self.make({"listen_mode": "always"})
        e.asr = mock.Mock(transcribe=mock.Mock(return_value="open the files"))
        e.brain = mock.Mock(handle=mock.Mock(return_value={"id": "t1", "latency": {}}))
        e._one("audio", object(), 0.0)
        self.assertEqual(self.partials(events), [("open the files", 3, True)])
        kinds = [ev.get("ev") for ev in events]
        self.assertLess(kinds.index("partial"), kinds.index("turn"))

    def test_nothing_to_clear_sends_nothing(self):
        e, events = self.make()
        e._clear_live()
        self.assertEqual(self.partials(events), [])


if __name__ == "__main__":
    sys.exit(unittest.main())
