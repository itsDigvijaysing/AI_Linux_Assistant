"""The engine's state machine under seeded random sequences of events, with no microphone, speaker or models.

The real Engine starts through its own ``_load`` and runs its Listen, Work and Speak threads. Audio, speech
recognition, speech synthesis and the brain are ``mock.create_autospec`` fakes of the real classes, so a call to a
method that doesn't exist fails (a plain Mock hid a quit crash on 2026-10-31). The mic queue is fed frame by frame
the way PipeWireAudioIO feeds it; each utterance's frames carry a code the fake recogniser turns back into words.

More seeds: PHOENIX_STATE_SEEDS=500 python -m unittest tests.test_states
"""

from __future__ import annotations

import importlib.util
import logging
import os
import queue
import random
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

for _mod in ("numpy", "onnxruntime", "soundfile", "yaml"):
    if importlib.util.find_spec(_mod) is None:
        raise unittest.SkipTest(f"needs {_mod} (the engine's requirements)")

import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import schema  # noqa: E402
from ai_linux.engine import service  # noqa: E402
from ai_linux.engine.asr.tdt_asr import AudioTranscriber  # noqa: E402
from ai_linux.engine.audio import PipeWireAudioIO  # noqa: E402
from ai_linux.engine.brain import Brain  # noqa: E402
from ai_linux.engine.tts import SpeechSynthesizer  # noqa: E402
from ai_linux.knowledge import Knowledge  # noqa: E402
from ai_linux.safety.gate import ConfirmRequest  # noqa: E402

FRAME = 0.002  # one mic frame here; FRAME_S is patched to match, so frame counts mean what they mean live
SILENT, VOICE_BASE, VOICE_STEP = 0.002, 0.1, 0.01  # frame values: silence, then one level per utterance code
RATE = 16000
REPLY_S = 0.12  # how long a spoken sentence plays
TIMES = {"FRAME_S": FRAME, "FOLLOWUP_S": 0.25, "LISTEN_NOW_S": 0.25, "PTT_END_S": 0.03, "CONFIRM_S": 0.6,
         "DICTATE_IDLE_S": 0.5, "ECHO_TAIL_S": 0.025, "NAME_TEST_S": 0.6}
MODES = ("wake", "always", "hotkey", "off")
FIRST = int(os.environ.get("PHOENIX_STATE_FIRST", "0"))
SEEDS = int(os.environ.get("PHOENIX_STATE_SEEDS", "6"))
EVENTS = 14  # per sequence
VOICE = {"name", "request", "cough", "barge", "dictate", "dict_text", "dict_stop", "confirm", "chatter", "sleep"}


class Stuck(AssertionError):
    pass


class Rig:
    """One engine with fake hardware and models, and the record of what reached them."""

    def __init__(self, mode: str = "wake", aec: bool = True, armed: bool = True, spoken_confirm: bool = True,
                 addressed_check: bool = False, think: float = 0.03, warm: float = 0.0):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # a quit while starting: timers write on
        self.patches = [mock.patch.object(service, k, v) for k, v in TIMES.items()]
        self.patches += [
            mock.patch("ai_linux.engine.audio.PipeWireAudioIO", autospec=True),
            mock.patch("ai_linux.engine.brain.Brain", autospec=True),
            mock.patch("ai_linux.engine.tts.SpeechSynthesizer", autospec=True),
            mock.patch("ai_linux.knowledge.Knowledge", autospec=True),
            mock.patch("ai_linux.engine.asr.get_transcriber"),
            mock.patch("ai_linux.engine.tools.paste_text", autospec=True),
            mock.patch("ai_linux.engine.llm.unload", autospec=True),
            mock.patch("ai_linux.engine.sounds.play", autospec=True),
            mock.patch("ai_linux.engine.mcp_client.close_all", autospec=True),
            mock.patch("ai_linux.paths.STATE_DIR", Path(self.tmp.name)),
            mock.patch.object(service.subprocess, "Popen", autospec=True),
        ]
        mocks = [p.start() for p in self.patches]
        audio_cls, brain_cls, tts_cls, _know, get_asr, self.paste, self.unload = mocks[len(TIMES):len(TIMES) + 7]
        from ai_linux.engine import tools as T
        self.paste.return_value = T.Result(True, "")

        self.think = think  # how long Brain.handle works before it answers
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.handled: list[tuple[str, str]] = []  # (text, source) given to Brain.handle
        self.texts: dict[int, str] = {}  # utterance code -> what the recogniser hears
        self.lengths: dict[int, int] = {}  # utterance code -> its voiced frames
        self.script: list[float] = []  # frames still to be said
        self.mic_open = False
        self.frames = queue.Queue()
        self.fed = 0  # frames put on the queue
        self.dictated_words: set[str] = set()  # said while dictating: never a request
        self.confirm_ids: list[str] = []

        audio = audio_cls.return_value
        audio._aec_ok = aec
        audio.get_sample_queue.return_value = self.frames
        audio.start_listening.side_effect = self._mic_on
        audio.stop_listening.side_effect = self._mic_off
        self.playing = threading.Event()
        self.stop_play = threading.Event()
        audio.start_speaking.side_effect = self._start_speaking
        audio.measure_percentage_spoken.side_effect = self._play
        audio.stop_speaking.side_effect = lambda: self.stop_play.set()
        self.audio = audio

        tts = tts_cls.return_value
        tts.sample_rate = RATE
        tts.generate_speech_audio.side_effect = lambda text, speed=1.05: np.full(int(RATE * REPLY_S), 0.1, np.float32)

        asr = mock.create_autospec(AudioTranscriber, instance=True)
        asr.device, asr.device_error = "cpu", ""
        asr.transcribe.side_effect = self._transcribe
        get_asr.return_value = asr
        self.asr = asr

        brain = brain_cls.return_value
        brain.extra_tools = []

        def made(setting, env, **kw):
            brain.env, self.speak = env, kw["speak"]
            return brain
        brain_cls.side_effect = made
        brain.handle.side_effect = self._handle
        brain.yes_no.side_effect = self._yes_no
        brain.addressed.return_value = True
        brain.polish.side_effect = lambda text, cancel=None: text
        brain.warm.side_effect = lambda step=None: time.sleep(warm)  # warm > 0: events come while starting
        self.brain = brain

        self.log = _Collect(self.errors)
        logging.getLogger("ai_linux").addHandler(self.log)

        self.engine = service.Engine(self._emit, {
            "listen_mode": mode, "wake_word": "phoenix", "actions_armed": armed, "spoken_confirm": spoken_confirm,
            "addressed_check": addressed_check, "mcp_servers": []})
        self.done = threading.Event()
        self.feeder = threading.Thread(target=self._feed, name="Feed", daemon=True)
        self.feeder.start()
        self.working = 0
        self.engine._one = self._counted(self.engine._one)
        self.ready = threading.Event()  # start-up finished: the mic is the listen mode's from here on
        get_ready = self.engine._get_ready
        self.engine._get_ready = lambda: (get_ready(), self.ready.set())
        self.glitches: list[str] = []  # wrong states seen while a request was being answered
        threading.Thread(target=self._watch, name="Watch", daemon=True).start()
        self.engine.start()
        if warm:
            self.until(lambda: self.engine._live is not None, "the threads never started")
        else:
            self.until(lambda: self.engine.state != "loading", "start-up never finished")

    # fakes

    def _emit(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)
        if ev.get("ev") == "confirm":
            self.confirm_ids.append(ev["id"])

    def _mic_on(self):
        with self.lock:
            self.mic_open = True
            while not self.frames.empty():
                self.frames.get_nowait()

    def _mic_off(self):
        with self.lock:
            self.mic_open = False
            self.script.clear()  # said with the mic closed: never heard

    def _start_speaking(self, audio, sample_rate=None, text=""):
        self.stop_play = threading.Event()
        self.playing.set()

    def _play(self, total, sample_rate=None):
        stopped = self.stop_play.wait(total / (sample_rate or RATE))
        self.playing.clear()
        return (True, 50) if stopped else (False, 100)

    def _transcribe(self, audio) -> str:
        """The words of the utterance the recording holds most of; a few frames of one are only a sound."""
        codes = np.round((audio[::512] - VOICE_BASE) / VOICE_STEP).astype(int)
        codes = codes[audio[::512] > VOICE_BASE - VOICE_STEP / 2]
        if not len(codes):
            return ""
        counts = np.bincount(codes)
        code = int(counts.argmax())
        return self.texts.get(code, "") if counts[code] >= 0.6 * self.lengths.get(code, 0) else ""

    def _yes_no(self, question, reply):
        w = reply.lower().strip(" .!?")
        return True if w in ("yes", "yes please", "sure") else False if w in ("no", "no thanks") else None

    def _handle(self, text, source="voice", cancel=None):
        with self.lock:
            self.handled.append((text, source))
        env = self.brain.env
        reply = f"Here is {text}."
        if "delete" in text:
            ok = env.confirm(ConfirmRequest("shell.run_command", {}, "rm -r old_logs", ""))
            reply = "Done." if ok else "Okay, I won't."
        if "go to sleep" in text:
            env.sleep()
            reply = "Good night."
        if cancel is not None and cancel.wait(self.think):
            return {"id": "cancelled", "latency": {}}
        self.speak(reply)
        return {"id": f"t{len(self.handled)}", "latency": {}}

    def _watch(self):
        """Samples the state mid-turn: a request being answered and not cancelled shows thinking or more."""
        e = self.engine
        while not self.done.is_set():
            time.sleep(0.002)
            turn = e._busy and not e.cancel.is_set() and not e.speaking and not e.confirming and not e._hold.is_set()
            muted_ok = e.s.get("listen_mode") == "off"
            if turn and (e.state in ("idle", "followup") or (e.state == "muted" and not muted_ok)):
                time.sleep(0.01)  # the same a moment later: not a step between two lines
                if e._busy and not e.cancel.is_set() and not e.speaking and e.state in ("idle", "followup"):
                    self.glitches.append(f"{e.state} while a request was answered: {self.describe()}")

    def _feed(self):
        frame_ids = {}
        while not self.done.is_set():
            with self.lock:
                if not self.mic_open:
                    value = None
                else:
                    value, prob = (self.script.pop(0), 0.9) if self.script else (SILENT, 0.0)
            if value is None:
                time.sleep(FRAME)
                continue
            frame = frame_ids.get(value)
            if frame is None:
                frame = frame_ids[value] = np.full(512, value, np.float32)
            self.frames.put((frame, prob))
            with self.lock:
                self.fed += 1
            time.sleep(FRAME)

    def _counted(self, one):
        def run(*a):
            with self.lock:
                self.working += 1
            try:
                return one(*a)
            finally:
                with self.lock:
                    self.working -= 1
        return run

    # driving

    def say(self, text: str, voiced: int = 20) -> None:
        """``voiced`` frames of speech the recogniser hears as ``text``, then silence."""
        code = len(self.texts) + 1
        self.texts[code], self.lengths[code] = text, voiced
        # the engine's fixed 0.3 s of deafness after speaking without echo cancelling isn't scaled like the rest
        self.until(lambda: time.monotonic() > self.engine.quiet_until, "deaf for too long")
        with self.lock:
            if self.mic_open:
                self.script.extend([VOICE_BASE + code * VOICE_STEP] * voiced)
        self.heard_out()

    def heard_out(self) -> None:
        """Waits until what was said is over and the silence after it would end any recording."""
        def over():
            with self.lock:
                return not self.script
        self.until(over, "the mic never took the frames")
        start = self.fed
        need = service.DICTATE_END_SILENCE + 6
        self.until(lambda: self.fed - start >= need or not self.mic_open, "frames stopped")
        self.until(lambda: self.frames.qsize() < 2 or not self.mic_open, "the listen loop stopped taking frames")

    def step(self, timeout: float = 3.0) -> None:
        """Until the threads have done what the last event started (a question waiting for its answer counts)."""
        e = self.engine

        def quiet():
            waiting = e.confirming or bool(e._confirms)
            recording = e._live_open and not e.ptt  # a held key records on purpose
            return (e.work.empty() and (self.working == 0 or waiting) and e.say_q.empty() and not recording
                    and (not e.speaking or waiting) and not self.playing.is_set())
        self.until(quiet, "the engine never went quiet", timeout)
        time.sleep(0.02)
        self.until(quiet, "the engine never went quiet", timeout)

    def settle(self) -> None:
        """Silence: every window runs out and whatever was going on ends."""
        e = self.engine
        self.step()
        deadline = time.monotonic() + 7  # a question's answer window is a fixed 5 s, not scaled
        while True:
            now = time.monotonic()
            busy = (not self.ready.is_set() or now < e.followup_until or now < e.armed_until
                    or now < e.quiet_until + 0.15 or e.dictating or e.confirming or e._confirms or self.working
                    or not e.work.empty() or not e.say_q.empty() or e.speaking or e.ptt or e._live_open)
            if not busy:
                break
            if now > deadline:
                raise Stuck(f"still busy after the silence: {self.describe()}")
            time.sleep(0.02)
        self.step()
        time.sleep(0.03)

    def until(self, check, what: str, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not check():
            if time.monotonic() > deadline:
                raise Stuck(f"{what}: {self.describe()}")
            time.sleep(0.003)

    def describe(self) -> str:
        e = self.engine
        now = time.monotonic()
        return (f"state={e.state} mode={e.s.get('listen_mode')} mic={self.mic_open} capturing={e.capturing} "
                f"speaking={e.speaking} dictating={e.dictating!r} confirming={e.confirming} ptt={e.ptt} "
                f"followup={max(0.0, e.followup_until - now):.2f}s armed={max(0.0, e.armed_until - now):.2f}s "
                f"work={e.work.qsize()} say={e.say_q.qsize()} working={self.working}")

    def quit(self, timeout: float = 3.0) -> float:
        t0 = time.monotonic()
        t = threading.Thread(target=self.engine.stop, name="Quit", daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise Stuck(f"quitting never finished: {self.describe()}")
        return time.monotonic() - t0

    def close(self) -> None:
        self.done.set()
        self.engine._stop.set()
        self.engine._capture_on.set()
        if self.engine.timers is not None:
            self.engine.timers.close()
        logging.getLogger("ai_linux").removeHandler(self.log)
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class _Collect(logging.Handler):
    def __init__(self, into: list):
        super().__init__(logging.ERROR)
        self.into = into

    def emit(self, record):
        self.into.append(f"{record.getMessage()}\n{record.exc_text or ''}")
        if record.exc_info:
            import traceback
            self.into.append("".join(traceback.format_exception(*record.exc_info)))


# the random sequences

class Script:
    """Seeded events with the bookkeeping the invariants need."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.seed = seed
        r = self.rng
        self.mode = r.choices(MODES, (3, 2, 2, 1))[0]
        self.muted = False
        self.config = {"mode": self.mode, "aec": r.random() < 0.6, "armed": r.random() < 0.85,
                       "spoken_confirm": r.random() < 0.7, "addressed_check": r.random() < 0.2,
                       "think": r.choice([0.03, 0.2]), "warm": r.choice([0.0, 0.0, 0.4])}
        self.log: list[str] = []
        self.n = 0
        self.typed: list[str] = []  # tags of typed requests: each runs exactly once

    def tag(self, kind: str) -> str:
        self.n += 1
        return f"{kind}{self.n}"

    def effective(self) -> str:
        return "off" if self.muted else self.mode

    def run(self, rig: Rig, events: int) -> bool:
        """True when it ended by quitting, whatever was going on."""
        r = self.rng
        for _ in range(events):
            kinds = {"name": 3, "request": 6, "cough": 3, "silence": 3, "barge": 3, "cancel": 2, "hotkey": 3,
                     "ptt": 3, "ptt_hold": 1, "mode": 2, "mute": 2, "profile": 1, "dictate": 4, "dict_text": 1,
                     "dict_stop": 1, "ring": 2, "confirm": 4, "typed": 3, "chatter": 2, "sleep": 1, "quit": 1}
            if rig.engine.dictating:
                kinds.update(dict_text=10, dict_stop=3, dictate=1)
            if rig.engine.ptt:
                kinds.update(ptt_hold=0, ptt_release=4)
            names, weights = zip(*kinds.items())
            kind = r.choices(names, weights)[0]
            if kind == "quit":
                return True
            if kind in VOICE and not rig.mic_open and r.random() < 0.7:
                self.note("the hotkey first")
                rig.engine.listen_now()
            getattr(self, "ev_" + kind)(rig)
            if r.random() < 0.35:
                self.note("  (the next event comes at once)")
                continue
            rig.step()
            check_always(self, rig)
        return False

    def note(self, text: str) -> None:
        self.log.append(text)

    def request_text(self, tag: str) -> str:
        return ("phoenix " if self.rng.random() < 0.7 else "") + f"what is {tag}"

    def ev_name(self, rig):
        self.note("the name alone")
        rig.say("phoenix", self.rng.randint(4, 12))

    def ev_request(self, rig):
        tag = self.tag("req")
        text = self.request_text(tag)
        e = rig.engine
        free = (e.state == "idle" and rig.mic_open and not rig.working and e.work.empty() and e.say_q.empty()
                and not e.dictating and not e.speaking and not e.ptt and time.monotonic() > e.quiet_until + 0.05)
        self.note(f"said {text!r}" + (" while idle" if free else ""))
        rig.say(text, self.rng.randint(12, 40))
        if free and text.startswith("phoenix"):
            rig.step()
            assert tag in _tags(rig), f"a request with the name, said while idle, never ran"

    def ev_sleep(self, rig):
        self.note("said 'phoenix go to sleep'")
        rig.say("phoenix go to sleep", 20)

    def ev_chatter(self, rig):
        text = f"talking to someone else about {self.tag('chat')}"
        self.note(f"said {text!r}")
        rig.say(text, self.rng.randint(12, 30))

    def ev_cough(self, rig):
        self.note("a cough")
        rig.say(self.rng.choice(["", "hmm", "um"]), self.rng.randint(2, service.MIN_SPEECH - 1))

    def ev_silence(self, rig):
        self.release(rig)
        self.note("silence")
        rig.settle()
        check_settled(self, rig)

    def ev_barge(self, rig):
        tag = self.tag("req")
        self.note(f"a request ({tag}), then talking over the reply")
        rig.say(f"phoenix what is {tag}", 20)
        try:
            rig.until(lambda: rig.playing.is_set(), "no reply", 1.0)
        except Stuck:
            self.note("  (no reply came to talk over)")
            return
        rig.say(f"no wait what about {self.tag('req')}", 30)

    def ev_cancel(self, rig):
        self.note("Cancel")
        rig.engine.cancel_turn()

    def ev_hotkey(self, rig):
        self.note("the hotkey")
        rig.engine.listen_now()
        if self.rng.random() < 0.6:
            text = f"what is {self.tag('req')}"
            self.note(f"  then said {text!r}")
            rig.say(text, 20)

    def ev_ptt(self, rig):
        self.note("hold-to-talk pressed")
        rig.engine.push_to_talk(True)
        if self.rng.random() < 0.7:
            text = f"what is {self.tag('req')}"
            self.note(f"  said {text!r} while holding")
            rig.say(text, 20)
        self.note("  released")
        rig.engine.push_to_talk(False)

    def ev_ptt_hold(self, rig):
        self.note("hold-to-talk pressed (and held)")
        rig.engine.push_to_talk(True)

    def ev_ptt_release(self, rig):
        self.release(rig)

    def release(self, rig):
        if rig.engine.ptt:
            self.note("hold-to-talk released")
            rig.engine.push_to_talk(False)

    def ev_mode(self, rig):
        self.mode = self.rng.choice(MODES)
        self.note(f"listen mode {self.mode}")
        rig.engine.update_settings({"listen_mode": self.effective()})

    def ev_mute(self, rig):
        self.muted = not self.muted
        self.note("Mute" if self.muted else "Unmute")
        rig.engine.update_settings({"listen_mode": self.effective()})

    def ev_profile(self, rig):
        self.mode = self.rng.choice(MODES)
        speed = self.rng.choice(schema.CHOICES.get("speed", ("efficient", "auto", "full")))
        self.note(f"power profile change (listen mode {self.mode}, speed {speed})")
        rig.engine.update_settings({"listen_mode": self.effective(), **schema.loading(speed),
                                    "small_model": self.rng.choice(["none", "jiwo-0.8b"])})

    def ev_dictate(self, rig):
        text = self.rng.choice(["phoenix dictate", "phoenix start dictating", "phoenix let's dictate"])
        self.note(f"said {text!r}")
        rig.say(text, 15)

    def ev_dict_text(self, rig):
        tag = self.tag("dw")
        e = rig.engine
        rig.until(lambda: time.monotonic() > e.quiet_until, "deaf for too long")
        left = e.followup_until - time.monotonic()  # the patched idle limit is short: not at its very end
        sure = e.dictating and left > 0.2 and e.work.empty() and not rig.working  # and no typed stop on its way
        if sure:
            rig.dictated_words.add(tag)
        self.note(f"said 'buy {tag} milk'" + (" while dictating" if sure else ""))
        rig.say(f"buy {tag} milk", self.rng.randint(12, 40))

    def ev_dict_stop(self, rig):
        self.note("said 'stop dictating'")
        rig.say("stop dictating", 14)

    def ev_ring(self, rig):
        self.note("a timer rang")
        rig.engine.say_q.put(("ring", "Reminder: stretch."))  # what Engine._ring queues after its sound

    def ev_confirm(self, rig):
        tag = self.tag("req")
        text = f"phoenix delete {tag}"
        self.note(f"said {text!r} (asks yes or no)")
        before = len(rig.confirm_ids)
        rig.say(text, 25)
        try:
            rig.until(lambda: len(rig.confirm_ids) > before, "no question", 1.0)
        except Stuck:
            self.note("  (no question was asked)")
            return
        how = self.rng.choice(["yes", "no", "button yes", "button no", "timeout", "other"])
        self.note(f"  answered: {how}")
        if how in ("yes", "no"):
            rig.say(how, 10)
        elif how.startswith("button"):
            rig.engine.confirm_reply(rig.confirm_ids[-1], how.endswith("yes"))
        elif how == "other":
            rig.say(f"what is {self.tag('req')}", 20)
        else:
            rig.until(lambda: not rig.engine._confirms, "the question never timed out", 3.0)

    def ev_typed(self, rig):
        if rig.engine.dictating and self.rng.random() < 0.4:
            self.note("typed 'stop dictating'")
            rig.engine.submit_text("stop dictating")
            return
        tag = self.tag("typed")
        self.typed.append(tag)
        self.note(f"typed 'what is {tag}'")
        rig.engine.submit_text(f"what is {tag}")


def _tags(rig: Rig) -> list[str]:
    """The request tags Brain.handle got, one per call that carried one."""
    return [w for text, _src in rig.handled for w in re.findall(r"\b(?:req|typed|dw)\d+\b", text)]


def check_always(script: Script, rig: Rig) -> None:
    """True after every event."""
    assert not rig.errors, "errors logged:\n" + "\n".join(rig.errors)
    assert not rig.glitches, rig.glitches[0]
    for text, _src in rig.handled:
        for w in text.split():
            assert w not in rig.dictated_words, f"dictated words reached Brain.handle: {text!r}"
    tags = _tags(rig)
    dup = {t for t in tags if tags.count(t) > 1}
    assert not dup, f"one request ran more than once: {sorted(dup)}"
    typed = [w for c in rig.paste.call_args_list for w in re.findall(r"\bdw\d+\b", c.args[1])]
    dup = {t for t in typed if typed.count(t) > 1}
    assert not dup, f"dictated words were typed more than once: {sorted(dup)}"
    e = rig.engine
    if e.state == "loading":
        assert not rig.mic_open, "the mic opened while starting"
    now = time.monotonic()
    mode = e.s.get("listen_mode")
    if rig.ready.is_set() and not e._stop.is_set() and e.state == "idle" and mode in ("wake", "always"):
        assert rig.mic_open, f"idle in listen mode {mode} with the mic closed"
    going_on = (e.speaking or e._busy or e.confirming or e.dictating or e.ptt or now < e.armed_until
                or now < e.followup_until or rig.working)
    if mode == "off" and e.state != "loading" and not going_on:
        assert e.state == "muted", f"muted but shows {e.state}"


def check_settled(script: Script, rig: Rig) -> None:
    """True once everything has ended: the listen mode alone decides the mic and the state."""
    e = rig.engine
    mode = e.s.get("listen_mode")
    assert mode == script.effective(), f"engine has listen mode {mode}, the app sent {script.effective()}"
    want = "muted" if mode == "off" else "idle"
    assert e.state == want, f"left on {e.state} with nothing going on: {rig.describe()}"
    mic = mode in ("wake", "always")
    assert rig.mic_open == mic, f"mic {'open' if rig.mic_open else 'closed'} in listen mode {mode}"
    assert e.capturing == mic, f"engine thinks the mic is {'open' if e.capturing else 'closed'} in {mode}"
    assert not e._hold.is_set(), "a paused reply is still held"
    assert not e.dictating and not e._dictated, "dictation still on"
    for tag in script.typed:
        assert tag in _tags(rig), f"typed request {tag} never ran"


class StateSequenceTests(unittest.TestCase):
    """Random sequences; a failure prints the seed and its events, and each bug found has its own test below."""

    def run_seed(self, seed: int) -> None:
        script = Script(seed)
        rig = Rig(script.config["mode"], script.config["aec"], script.config["armed"],
                  script.config["spoken_confirm"], script.config["addressed_check"], script.config["think"],
                  script.config["warm"])
        try:
            if not script.run(rig, EVENTS):
                script.release(rig)
                script.note("silence")
                rig.settle()
                check_settled(script, rig)
            script.note("quit")
            took = rig.quit()
            assert took < 3.0, f"quitting took {took:.1f}s"
            check_always(script, rig)
            assert not rig.mic_open, "the mic stayed open after quitting"
        except AssertionError as exc:
            raise AssertionError(f"seed {seed} {script.config}:\n  " + "\n  ".join(script.log)
                                 + f"\n{type(exc).__name__}: {exc}") from None
        finally:
            rig.close()

    def test_random_sequences(self):
        for seed in range(FIRST, FIRST + SEEDS):
            with self.subTest(seed=seed):
                self.run_seed(seed)


class FoundTests(unittest.TestCase):
    """One test per bug the random sequences found."""

    def rig(self, mode="wake", **kw) -> Rig:
        rig = Rig(mode, **kw)
        self.addCleanup(rig.close)
        return rig

    def in_window(self, rig: Rig) -> None:
        rig.engine.submit_text("what is the time")
        rig.step()
        self.assertEqual(rig.engine.state, "followup")

    def assert_rests(self, rig: Rig, state: str, mic: bool) -> None:
        rig.settle()
        self.assertEqual((rig.engine.state, rig.mic_open), (state, mic), rig.describe())

    def test_hotkey_mode_in_a_follow_up_window_stays_on_followup(self):
        rig = self.rig("wake")
        self.in_window(rig)
        rig.engine.update_settings({"listen_mode": "hotkey"})
        self.assert_rests(rig, "idle", False)

    def test_a_timer_after_mute_in_a_follow_up_window_shows_followup(self):
        rig = self.rig("wake")
        self.in_window(rig)
        rig.engine.update_settings({"listen_mode": "off"})
        rig.engine.say_q.put(("ring", "Reminder: stretch."))
        rig.step()
        self.assert_rests(rig, "muted", False)

    def test_hotkey_mode_while_the_hotkey_listens_stays_on_listening(self):
        rig = self.rig("wake")
        rig.engine.listen_now()
        rig.engine.update_settings({"listen_mode": "hotkey"})
        self.assert_rests(rig, "idle", False)


    def test_a_reply_during_dictation_cuts_its_window_to_the_follow_up_window(self):
        rig = self.rig("wake")
        self.enterContext(mock.patch.object(service, "DICTATE_IDLE_S", 2.0))
        rig.say("phoenix dictate", 15)
        rig.step()
        rig.engine.submit_text("what is the time")  # typed in the app while dictating
        rig.step()
        time.sleep(service.FOLLOWUP_S + 0.1)  # still well inside DICTATE_IDLE_S
        self.assertEqual((rig.engine.dictating, rig.engine.state), ("polish", "followup"), rig.describe())
        self.assertEqual([ev["detail"] for ev in rig.events if ev.get("ev") == "state"][-1], "Dictating")


    def test_the_hotkey_during_a_spoken_question_leaves_listening_with_the_mic_closed(self):
        rig = self.rig("hotkey")
        rig.engine.listen_now()
        rig.say("delete the old logs", 25)
        rig.until(lambda: rig.engine.confirming and rig.playing.is_set(), "no question")
        rig.engine.listen_now()  # pressed to answer: the question is dropped, the hotkey listens
        rig.until(lambda: not rig.engine.confirming, "the question went on")
        rig.say("what is the time", 20)
        rig.step()
        self.assertIn(("what is the time", "voice"), rig.handled, rig.describe())
        self.assert_rests(rig, "idle", False)


    def test_a_timer_ringing_while_thinking_shows_idle(self):
        rig = self.rig("wake", think=0.6)
        rig.engine.submit_text("what is the time")
        rig.until(lambda: rig.engine.state == "thinking", "never thought")
        rig.engine.say_q.put(("ring", "Reminder: stretch."))
        rig.until(lambda: rig.playing.is_set(), "no ring")
        rig.until(lambda: not rig.playing.is_set(), "the ring never ended")
        time.sleep(0.05)
        self.assertTrue(rig.engine._busy)  # still working on the answer
        self.assertEqual(rig.engine.state, "thinking", rig.describe())


    def test_the_end_of_a_reply_shows_followup_while_the_next_request_is_answered(self):
        rig = self.rig("wake", think=0.5)
        rig.engine.submit_text("what is the time")
        rig.until(lambda: rig.playing.is_set(), "no reply")
        rig.engine.submit_text("what is the date")  # typed while the first answer is spoken
        rig.until(lambda: not rig.playing.is_set(), "the reply never ended")
        time.sleep(0.05)
        self.assertTrue(rig.engine._busy)  # still working on the second answer
        self.assertEqual(rig.engine.state, "thinking", rig.describe())


    def test_a_cough_while_a_request_is_answered_shows_idle(self):
        rig = self.rig("always", think=0.8)
        rig.engine.submit_text("what is the time")
        rig.until(lambda: rig.engine.state == "thinking", "never thought")
        rig.say("", 5)  # a cough: listening, then dropped as too short
        time.sleep(0.05)
        self.assertTrue(rig.engine._busy)  # still working on the answer
        self.assertEqual(rig.engine.state, "thinking", rig.describe())


    def test_the_name_said_over_a_reply_while_muted_stays_on_followup(self):
        rig = self.rig("off", aec=True, think=0.1)
        rig.engine.listen_now()  # muted, but the hotkey still listens
        rig.say("what is the time", 20)
        rig.until(lambda: rig.playing.is_set(), "no reply")
        rig.say("phoenix", 11)  # the name alone, over the reply
        rig.step()
        self.assert_rests(rig, "muted", False)


    def test_a_reply_ending_while_hold_to_talk_is_held_leaves_listening_with_the_mic_closed(self):
        rig = self.rig("off")
        self.enterContext(mock.patch.object(service, "MAX_FRAMES", 60))  # a long hold is cut into requests
        rig.engine.push_to_talk(True)
        rig.say("what is the time", 40)
        rig.until(lambda: rig.handled, "the cut recording never ran")
        rig.step()  # its reply ends while the key is still held
        self.assertEqual((rig.engine.state, rig.mic_open), ("listening", True), rig.describe())
        rig.engine.say_q.put(("ring", "Reminder: stretch."))
        rig.step()
        self.assertEqual((rig.engine.state, rig.mic_open), ("listening", True), rig.describe())
        rig.engine.push_to_talk(False)
        self.assert_rests(rig, "muted", False)


    def test_a_long_hold_to_talk_in_hotkey_mode_leaves_listening_with_the_mic_closed(self):
        rig = self.rig("hotkey")
        self.enterContext(mock.patch.object(service, "MAX_FRAMES", 60))  # a long hold is cut into requests
        rig.engine.push_to_talk(True)
        rig.say("what is the time", 40)
        rig.until(lambda: rig.handled, "the cut recording never ran")
        rig.step()
        self.assertTrue(rig.mic_open, "the mic closed under the held key")
        rig.engine.push_to_talk(False)
        self.assert_rests(rig, "idle", False)


if __name__ == "__main__":
    unittest.main()
