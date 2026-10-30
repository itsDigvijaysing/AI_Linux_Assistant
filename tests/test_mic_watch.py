"""The dead-microphone warning: digital silence or no frames at all, never a quiet but working mic."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import mic_watch  # noqa: E402
from ai_linux.engine.mic_watch import SilenceWatch  # noqa: E402

QUIET_ROOM = 51 / 32768  # the quietest frame measured through echo cancelling on this laptop
FRAME_S = 0.032


def feed(watch, peak, seconds, start=0.0):
    out, t = [], start
    while t < start + seconds:
        out.append(watch.frame(peak, t))
        t += FRAME_S
    return [v for v in out if v], t


class SilenceWatchTests(unittest.TestCase):
    def test_digital_silence_warns_once(self):
        w = SilenceWatch()
        verdicts, _ = feed(w, 0.0, 12)
        self.assertEqual(verdicts, ["dead"])

    def test_no_frames_at_all_warns(self):
        w = SilenceWatch()
        verdicts, _ = feed(w, None, 6)
        self.assertEqual(verdicts, ["dead"])

    def test_quiet_room_is_not_dead(self):
        w = SilenceWatch()
        verdicts, _ = feed(w, QUIET_ROOM, 30)
        self.assertEqual(verdicts, [])

    def test_waits_the_grace_period(self):
        w = SilenceWatch()
        verdicts, _ = feed(w, 0.0, mic_watch.DEAD_S - 0.5)
        self.assertEqual(verdicts, [])

    def test_recovery_is_reported_and_rearms(self):
        w = SilenceWatch()
        _, t = feed(w, 0.0, 6)
        verdicts, t = feed(w, QUIET_ROOM, 1, t)
        self.assertEqual(verdicts, ["back"])
        verdicts, _ = feed(w, 0.0, 6, t)
        self.assertEqual(verdicts, ["dead"])

    def test_restart_gives_a_new_grace_period(self):
        w = SilenceWatch()
        _, t = feed(w, 0.0, 4)
        w.restart(t)
        verdicts, _ = feed(w, 0.0, 4, t)
        self.assertEqual(verdicts, [])


class InputNameTests(unittest.TestCase):
    def test_reads_the_default_source_description(self):
        out = ('id 57, type PipeWire:Interface:Node\n'
               '    media.class = "Audio/Source"\n'
               '  * node.description = "C-Media(R) Audio Digital Stereo (IEC958)"\n')
        done = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("ai_linux.engine.mic_watch.subprocess.run", return_value=done):
            self.assertEqual(mic_watch.input_name(), "C-Media(R) Audio Digital Stereo (IEC958)")

    def test_missing_wpctl_gives_none(self):
        with mock.patch("ai_linux.engine.mic_watch.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(mic_watch.input_name())

    def test_text_names_the_input(self):
        self.assertIn('"Digital Stereo"', mic_watch.dead_text("Digital Stereo"))
        self.assertIn("Settings", mic_watch.dead_text(None))


if __name__ == "__main__":
    unittest.main()
