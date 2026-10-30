"""The engine: mic -> VAD -> wake word -> Parakeet -> brain -> SuperTonic -> speaker.

Runs as a child of the app (``python -m ai_linux.engine``) and talks over stdin/stdout, one JSON object per
line: commands in, events out (see PROTOCOL in __main__). Logs go to stderr.
"""

from __future__ import annotations

import collections
import logging
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from ai_linux.engine import mic_watch, sounds
from ai_linux.engine.live_text import LiveText, settled
from ai_linux.safety import ActionGate, Executor
from ai_linux.safety.gate import ConfirmRequest
from ai_linux.schema import DEFAULTS, on_ac_power  # noqa: F401  on_ac_power re-exported for tests
from ai_linux.wake import strip_wake, words as _words  # noqa: F401  strip_wake re-exported for tests

log = logging.getLogger(__name__)

FRAME_S = 0.032  # one 512-sample VAD frame at 16 kHz
PREROLL = 20  # frames kept before speech starts (~0.64 s)
END_SILENCE = 22  # silent frames that end an utterance (~0.7 s)
MAX_FRAMES = 470  # ~15 s
MIN_SPEECH = 8  # voiced frames for a real utterance (~0.25 s)
LISTEN_VAD = 0.5  # voice probability that starts a recording while we are silent (Silero's default)
BARGE_VAD = 0.3  # echo cancelling eats half the user's frames while we talk; our settled echo stays under 0.12
BARGE_WINDOW, BARGE_FRAMES = 12, 5  # 5 voiced of the last 12 frames (~0.4 s) pause the reply (2026-10-30 tests)
FALSE_BARGES = 2  # pauses that were only our own echo, per turn, before barge-in sits out the rest of the reply
FOLLOWUP_S = 10.0  # after a reply, follow-ups need no wake word
CONFIRM_S = 55.0  # a confirmation not answered by then is a no (the app's dialog gives up at the same time)
LISTEN_NOW_S = 8.0  # hotkey: how long to wait for speech to start
NAME_TEST_S = 20.0  # a name-test sentence must be heard within this
JUNK = {"", "yeah", "yes", "mm", "hmm", "uh", "um", "ah", "oh", "okay", "ok", "you", "the", "a", "and", "so"}


def echo_of(heard: str, said: str) -> bool:
    """True when the words heard are only filler or words we were saying: our own voice leaking past echo cancelling."""
    words = _words(heard)
    if not words or " ".join(words) in JUNK:
        return True
    ours = set(_words(said))
    return sum(w in ours for w in words) >= 0.7 * len(words)


class Engine:
    def __init__(self, emit, settings: dict):
        self.emit = emit
        self.s = {**DEFAULTS, **settings}
        self.audio = self.asr = self.tts = self.brain = None
        self.gate: ActionGate | None = None
        self.work: queue.Queue = queue.Queue()
        self.say_q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.state = "loading"
        self.capturing = False
        self.speaking = False
        self.armed_until = 0.0  # hotkey / listen_now: next utterance needs no wake word
        self.name_test_until = 0.0  # until then, the next utterance is only transcribed and sent back
        self.ptt = False
        self.followup_until = 0.0
        self.called = False  # the name alone was said: the request is still to come
        self.sleep_after = False  # "go to sleep": no follow-up window after this turn
        self.quiet_until = 0.0  # half-duplex: ignore the mic until our own voice has died away
        self._confirms: dict[str, tuple[threading.Event, dict]] = {}
        self.confirming = False
        self._hold = threading.Event()  # set while a barge-in has paused the reply and waits for its verdict
        self._hold_resume = False  # the verdict: it was our own echo, the reply goes on
        self._false_barges = 0
        self._saying = ""  # the sentence being spoken: an echo of it is not the user
        self._busy = False  # a turn is running
        self._think_detail = ""  # what the big model is doing, restored after "One moment."
        self.mcp = None
        self._mcp_lock = threading.Lock()  # two quick setting changes must not each keep a set of servers
        self.timers = None
        self._mic = mic_watch.SilenceWatch()
        self._mic_reopened = False  # one quiet re-open before warning
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._asr_lock = threading.Lock()  # one Parakeet decode at a time: live words, commands, warm-up
        self._live: LiveText | None = None
        self._utt = 0  # the utterance being recorded
        self._live_open = False
        self._live_prev: list[str] = []
        self._live_shown = False

    # settings and helpers

    def setting(self, key: str):
        return self.s.get(key)

    def set_state(self, state: str, detail: str = "") -> None:
        if state != self.state:
            log.info("state %s%s", state, f" ({detail})" if detail else "")
        if state != self.state or detail:
            self.state = state
            self.emit({"ev": "state", "state": state, "detail": detail})

    def notice(self, level: str, text: str) -> None:
        self.emit({"ev": "notice", "level": level, "text": text})

    # startup

    def start(self) -> None:
        threading.Thread(target=self._load, name="EngineLoad", daemon=True).start()

    def _load(self) -> None:
        from ai_linux.engine.audio import PipeWireAudioIO
        from ai_linux.engine.brain import Brain
        from ai_linux.engine.tools import Env
        from ai_linux.engine.tts import SpeechSynthesizer

        self.set_state("loading", "Loading speech models")
        self.gate = ActionGate(armed=bool(self.s.get("actions_armed")), confirm_globs=self._confirm_globs())
        from ai_linux.knowledge import Knowledge

        from ai_linux.engine.timers import Timers
        from ai_linux.paths import STATE_DIR

        self.timers = Timers(STATE_DIR / "timers.json", self._ring)
        env = Env(Executor(self.gate), confirm=self._confirm, set_voice=self._set_voice, sleep=self._sleep,
                  cue=self.cue, knowledge=Knowledge(on_change=lambda: self.emit({"ev": "knowledge"})),
                  setting=self.setting, timers=self.timers)
        try:
            self.tts = SpeechSynthesizer(self._voice_id(self.s.get("voice")))
            self.asr = self._new_asr()
            self.audio = PipeWireAudioIO()
        except Exception as exc:
            log.exception("loading speech models")
            self.set_state("error", f"Speech models failed to load: {exc}")
            return
        if not getattr(self.audio, "_aec_ok", False):
            self.notice("warning", "Echo cancelling isn't available, so you can't talk over the assistant.")
        self.brain = Brain(self.setting, env, speak=self._say, speak_wait=self._say_wait, status=self._big_status,
                           loaded=lambda what, d: self.emit({"ev": "model", "name": what, "detail": d}))
        self._live = LiveText(lambda audio: self.asr.transcribe(audio), self._asr_lock, self._on_live)
        for target, name in ((self._listen_loop, "Listen"), (self._work_loop, "Work"), (self._speak_loop, "Speak")):
            threading.Thread(target=target, name=name, daemon=True).start()
        threading.Thread(target=self._load_mcp, name="MCP", daemon=True).start()
        self.timers.start()
        self._get_ready()

    def _get_ready(self) -> None:
        """Stay "Starting" until speech recognition and the models the load mode wants are ready."""
        asr = threading.Thread(target=self._warm_asr, name="WarmSTT", daemon=True)
        asr.start()
        self.set_state("loading", "Starting speech recognition")
        try:
            self.brain.warm(step=lambda detail: self.state == "loading" and self.set_state("loading", detail))
        except Exception:
            log.exception("warming the models")  # the mic must open anyway; the next request loads them
        if asr.is_alive() and self.state == "loading":
            self.set_state("loading", "Starting speech recognition")
        asr.join()
        self.apply_listen_mode()  # also when a timer rang or typed text ran while starting

    def _big_status(self, detail: str) -> None:
        self._think_detail = detail
        if self.state == "thinking":  # never pull the pill back from speaking
            self.set_state("thinking", detail)

    def _load_mcp(self) -> None:
        from ai_linux.engine.mcp_client import McpTools
        with self._mcp_lock:
            old, self.mcp = self.mcp, None
            if old is not None:
                old.close()
            servers = [x for x in self.s.get("mcp_servers") or [] if isinstance(x, dict) and x.get("enabled", True)]
            if not servers:
                self.brain.extra_tools = []
                return
            self.mcp = McpTools(servers, notice=self.notice)
            self.brain.extra_tools = self.mcp.tools()

    def stop(self) -> None:
        self._stop.set()
        self.cancel_turn()
        if self.brain is not None:
            from ai_linux.engine import llm
            # Free the graphics card now: Ollama would keep the model for keep_alive, forever with "instant".
            llm.unload(self.brain.base, self.brain.big, timeout=2)
        if self.mcp is not None:
            self.mcp.close()
        if self.timers is not None:
            self.timers.close()
        if self.audio is not None:
            self.audio.stop_listening()
            self.audio._teardown_aec()

    # commands from the app

    def update_settings(self, values: dict) -> None:
        old = self.s
        self.s = {**old, **values}
        if self.gate is not None:
            self.gate.armed = bool(self.s.get("actions_armed"))
            self.gate.confirm_globs = self._confirm_globs()
        if self.s.get("voice") != old.get("voice") and self.tts is not None:
            self._set_voice(str(self.s.get("voice")), announce=False)
        stt = ("stt_engine", "stt_device")
        if any(self.s.get(k) != old.get(k) for k in stt) and self.asr is not None:
            threading.Thread(target=self._reload_asr, daemon=True).start()
        if self.s.get("listen_mode") != old.get("listen_mode") and self.audio is not None:
            self.apply_listen_mode()
        if self.brain is None:
            return
        changed = {k for k in values if old.get(k) != self.s.get(k)}
        big = {"big_model", "big_device", "context_length", "ollama_url"}
        if changed & big and old.get("big_model"):
            from ai_linux.engine import llm
            threading.Thread(target=llm.unload, args=(str(old.get("ollama_url")).rstrip("/"), old["big_model"]),
                             daemon=True).start()  # free the graphics card; the next request loads it again
        if "big_model_policy" in changed and self.s.get("big_model_policy") != "instant" and not changed & big:
            from ai_linux.engine import llm
            threading.Thread(target=llm.unload, args=(self.brain.base, self.brain.big),
                             daemon=True).start()  # battery-efficient again: it was kept loaded for good
        if changed & (big | {"small_model", "small_device", "big_model_policy"}):
            if changed & {"small_model", "small_device"}:
                self.brain.drop_small()
            threading.Thread(target=self.brain.warm, name="Warm", daemon=True).start()
        if "mcp_servers" in changed:
            threading.Thread(target=self._load_mcp, name="MCP", daemon=True).start()

    def _confirm_globs(self) -> list[str]:
        return ["shell.run_command"] if self.s.get("confirm_shell", True) else []

    def _new_asr(self):
        from ai_linux.engine.asr import get_transcriber
        asr = get_transcriber(str(self.s.get("stt_engine") or "parakeet-tdt"), str(self.s.get("stt_device") or "cpu"))
        device = getattr(asr, "device", "cpu")
        err = getattr(asr, "device_error", "")
        log.info("speech recognition on %s%s", device, f" ({err})" if err else "")
        detail = f"{device}, Intel GPU unavailable: {err}" if err else device
        self.emit({"ev": "model", "name": "speech", "detail": detail})
        return asr

    def _reload_asr(self) -> None:
        try:
            asr = self._new_asr()
        except Exception as exc:
            self.notice("error", f"Couldn't switch speech recognition: {exc}")
            return
        with self._asr_lock:
            self.asr = asr
        self._warm_asr()

    def _warm_asr(self) -> None:
        try:
            warm = getattr(self.asr, "warm", None)
            if warm is not None:
                warm(MAX_FRAMES * FRAME_S, self._asr_lock)
        except Exception:
            log.exception("warming speech recognition")

    # live words

    def _on_live(self, utt: int, text: str) -> None:
        """An update of the utterance so far. Speech that isn't for the assistant is never shown."""
        with self._lock:
            if not self._live_open or utt != self._utt or self.speaking:
                return
            wake = str(self.s.get("wake_word") or "phoenix")
            shown = text
            if not self._wake_free():
                shown = strip_wake(text, wake)
                if shown is None:
                    return
                if self.state != "listening":
                    self.set_state("listening")  # the name was heard: light up now, not after the sentence
            elif self._unasked_always_on():
                return  # always on: whether talk was for us is only known from the whole sentence
            elif self.s.get("wake_word"):
                shown = strip_wake(text, wake) or text
            self._emit_live(shown)

    def _unasked_always_on(self) -> bool:
        now = time.monotonic()
        asked = self.ptt or now < self.armed_until or now < self.followup_until
        return self.s.get("listen_mode") == "always" and bool(self.s.get("addressed_check")) and not asked

    def _emit_live(self, text: str, final: bool = False) -> None:
        words = text.split()
        stable = len(words) if final else settled(self._live_prev, words)
        self._live_prev = [] if final else words
        self._live_shown = bool(text)
        self.emit({"ev": "partial", "text": text, "stable": stable, "final": final})

    def _clear_live(self) -> None:
        if self._live_shown:
            self._emit_live("", final=True)

    def apply_listen_mode(self) -> None:
        mode = self.s.get("listen_mode")
        if mode in ("wake", "always"):
            self._capture(True)
            if self.state in ("loading", "muted", "off", "error"):
                self.set_state("idle")
        elif mode == "hotkey":
            self._capture(False)
            if self.state in ("loading", "muted", "off", "error"):
                self.set_state("idle")
        else:
            self._capture(False)
            self.set_state("muted")

    def _starting(self) -> bool:
        if self.state != "loading":
            return False
        self.notice("info", "Phoenix is still starting. Try again in a moment.")
        return True

    def listen_now(self) -> None:
        """Hotkey or tray: take the next thing said, no wake word."""
        if self._starting():
            return
        if self.speaking or self.state == "thinking":
            self.cancel_turn()
        self.armed_until = time.monotonic() + LISTEN_NOW_S
        self._capture(True)
        self._ping()
        self.set_state("listening")

    def name_test_listen(self) -> None:
        """Settings' name test: hear one sentence and send back what was heard."""
        self.name_test_until = time.monotonic() + NAME_TEST_S
        self.listen_now()

    def name_test_stop(self) -> None:
        if self.name_test_until:
            self.name_test_until = 0.0
            self.armed_until = 0.0
            self._mic_after_turn()
            self.set_state("idle")

    def _mic_after_turn(self) -> None:
        if self.s.get("listen_mode") not in ("wake", "always"):
            self._capture(False)

    def push_to_talk(self, pressed: bool) -> None:
        if pressed:
            if self._starting():
                return
            if self.speaking or self.state == "thinking":
                self.cancel_turn()
            self.ptt = True
            self._capture(True)
            self._ping()
            self.set_state("listening")
        else:
            self.ptt = False

    def submit_text(self, text: str) -> None:
        self.work.put(("text", text, 0.0))

    def new_session(self) -> None:
        """Home's New Session: the model forgets this conversation; History keeps it."""
        self.cancel_turn()
        if self.brain is not None:
            self.brain.new_session()

    def cancel_turn(self) -> None:
        self.cancel.set()
        self._hold.clear()  # a paused reply is dropped, not resumed
        for item in self._drain(self.say_q):
            if item[0] == "ring":  # a timer still rings after an interrupted reply
                self.say_q.put(item)
        if self.audio is not None:
            self.audio.stop_speaking()
        self.speaking = False
        if self.state in ("thinking", "speaking", "listening", "followup"):
            self.set_state("idle")

    def confirm_reply(self, cid: str, allowed: bool) -> None:
        item = self._confirms.get(cid)
        if item:
            item[1]["ok"] = bool(allowed)
            item[0].set()

    # capture

    def _capture(self, on: bool) -> None:
        with self._lock:
            if self.audio is None or on == self.capturing:
                return
            try:
                self.audio.start_listening() if on else self.audio.stop_listening()
                self.capturing = on
                if on:
                    self._mic.restart(time.monotonic())
            except Exception as exc:
                log.exception("microphone")
                self.notice("error", f"The microphone isn't available: {exc}")

    def _watch_mic(self, peak: float | None) -> None:
        if peak is not None and peak > mic_watch.FLOOR:
            self._mic_reopened = False
        verdict = self._mic.frame(peak, time.monotonic())
        if verdict == "dead" and not self._mic_reopened:
            # A stream opened after the device idled can stay silent; open it once more first (as OpenWhispr does).
            self._mic_reopened = True
            log.warning("microphone sends only silence; opening it again")
            self._capture(False)
            self._capture(True)
        elif verdict == "dead":
            name = mic_watch.input_name()
            log.warning("microphone sends only silence (input: %s)", name)
            self.notice("error", mic_watch.dead_text(name))
        elif verdict == "back":
            self.notice("info", "The microphone is working again.")

    def _wake_free(self, at: float | None = None) -> bool:
        """No wake word needed for speech that started at ``at`` (default now)."""
        now = time.monotonic() if at is None else at
        return (self.s.get("listen_mode") == "always" or self.ptt or now < self.armed_until
                or now < self.followup_until)

    def _pause_reply(self) -> None:
        """Barge-in: silence the reply but keep it until the recording shows whether the user spoke."""
        self._hold_resume = False
        self._hold.set()
        self.speaking = False
        if self.audio is not None:
            self.audio.stop_speaking()

    def _barge_verdict(self, audio: np.ndarray) -> None:
        """A paused reply's recording was short: the user if its words are not our own sentence leaking back."""
        try:
            with self._asr_lock:
                heard = (self.asr.transcribe(audio) or "").strip() if self.asr is not None else ""
        except Exception:
            log.exception("transcribing a barge-in")
            heard = ""
        if not self._hold.is_set():  # settled meanwhile (the user kept talking, or a cancel)
            return
        if echo_of(heard, self._saying):
            self._false_barges += 1
            log.info("barge-in was our own voice (%r): the reply goes on", heard)
            self._hold_resume = True
            self._hold.clear()
            return
        log.info("barge-in heard %r: the reply stops", heard)
        self.cancel_turn()
        self.work.put(("heard", heard, 0.0))

    def _held(self) -> bool:
        """Waits out a paused reply; True when it goes on."""
        if not self._hold.is_set():
            return True
        while self._hold.is_set() and not self._stop.is_set():
            time.sleep(0.02)
        return self._hold_resume

    def _listen_loop(self) -> None:
        pre = collections.deque(maxlen=PREROLL)
        rec: list[np.ndarray] = []
        voiced = silent = 0
        barge: collections.deque = collections.deque(maxlen=BARGE_WINDOW)
        barge_best, barge_peak, was_speaking = 0, 0.0, False
        recording = ptt_rec = False
        started = 0.0  # when the utterance being recorded began: the wake-word window is judged then
        loudest = 0.0
        last_level = 0.0
        while not self._stop.is_set():
            if not self.capturing or self.audio is None:
                time.sleep(0.05)
                pre.clear()
                if self._hold.is_set():
                    self.cancel_turn()
                if recording:
                    self._end_utterance()
                    self._clear_live()
                rec, recording, voiced, silent = [], False, 0, 0
                continue
            try:
                frame, prob = self.audio.get_sample_queue().get(timeout=0.2)
            except queue.Empty:
                self._watch_mic(None)
                if self.armed_until and time.monotonic() > self.armed_until and not recording and not self.ptt:
                    self._end_listen_now()
                if not recording:
                    self._check_followup()
                continue
            if not recording:
                self._check_followup()
            peak = float(np.abs(frame).max())
            self._watch_mic(peak)
            aec = getattr(self.audio, "_aec_ok", False)
            vad = float(prob) > (BARGE_VAD if self.speaking else LISTEN_VAD)
            if was_speaking and not self.speaking:
                if barge_peak > 0.3:  # numbers for tuning: how close the user came to interrupting
                    log.info("while speaking: voice up to %.2f, %d of %d frames", barge_peak, barge_best,
                             BARGE_WINDOW)
                barge.clear()
                barge_best, barge_peak = 0, 0.0
            was_speaking = self.speaking
            if self.speaking:
                if not (aec and self.s.get("barge_in", True)) or self._false_barges >= FALSE_BARGES:
                    continue  # half-duplex: the mic would hear our own voice
                barge.append(vad)
                barge_peak = max(barge_peak, float(prob))
                barge_best = max(barge_best, sum(barge))
                if sum(barge) < BARGE_FRAMES:
                    pre.append(frame)
                    continue
                log.info("barge-in (voice up to %.2f)", barge_peak)
                if self.confirming:  # the answer to our question, not an interruption
                    self._drain(self.say_q)
                    self.audio.stop_speaking()
                    self.speaking = False
                else:
                    self._pause_reply()
                    self.followup_until = time.monotonic() + FOLLOWUP_S
                barge.clear()
                vad = True  # the voice that interrupted starts the recording
            elif time.monotonic() < self.quiet_until:
                continue
            if self.ptt:
                vad = True  # hold-to-talk records everything until release
            if not recording:
                pre.append(frame)
                if vad:
                    recording, rec, voiced, silent = True, list(pre), 1, 0
                    started = time.monotonic() - len(pre) * FRAME_S
                    loudest = peak
                    ptt_rec = self.ptt
                    self._begin_utterance()
                    if self._wake_free():
                        self.set_state("listening")
                continue
            rec.append(frame)
            loudest = max(loudest, peak)
            voiced, silent = (voiced + 1, 0) if vad else (voiced, silent + 1)
            if self._hold.is_set() and voiced >= MIN_SPEECH:  # still talking with our voice gone: the user
                log.info("barge-in: the user kept talking, the reply stops")
                self.cancel_turn()
                self.set_state("listening")  # cancel_turn idles it, which hid the pill mid-sentence
            if self.state == "listening" and time.monotonic() - last_level > 0.03:
                last_level = time.monotonic()
                self.emit({"ev": "level", "v": round(float(np.sqrt(np.mean(frame ** 2))), 4), "src": "mic"})
            done = (silent >= END_SILENCE and not self.ptt) or len(rec) >= MAX_FRAMES or (ptt_rec and not self.ptt)
            live = self._live
            if (not done and live is not None and self.s.get("live_text", True) and silent < 8
                    and live.due(len(rec) * len(frame))):
                live.offer(self._utt, np.concatenate(rec))
            if done:
                self._end_utterance()
                audio = np.concatenate(rec)
                recording, rec, pre = False, [], collections.deque(maxlen=PREROLL)
                held = self._hold.is_set()
                log.info("recorded %.1fs, %d voiced frames, peak %.3f%s", len(audio) / 16000, voiced, loudest,
                         "" if voiced >= MIN_SPEECH else ", short: checking the words" if held else ", too short: dropped")
                if held:
                    self._barge_verdict(audio)
                elif voiced >= MIN_SPEECH:
                    self.work.put(("audio", audio, started))
                elif self.state == "listening" and not self._wake_free():
                    self.set_state("idle")
                self.emit({"ev": "level", "v": 0.0, "src": "mic"})

    def _begin_utterance(self) -> None:
        with self._lock:
            self._utt += 1
            self._live_open, self._live_prev = True, []
        if self._live is not None:
            self._live.begin(self._utt)

    def _end_utterance(self) -> None:
        with self._lock:
            self._live_open = False
        if self._live is not None:
            self._live.end()

    def _check_followup(self) -> None:
        """The window after the name or a reply closed with nothing said: back to waiting for the name."""
        if self.state != "followup" or self.speaking or time.monotonic() < self.followup_until:
            return
        self.set_state("idle")
        if self.called:
            self.called = False
            self.cue("sleep")  # woke, heard no request, going back to sleep

    def _called(self) -> None:
        """The name alone: chime and wait for the request."""
        self._clear_live()
        self.called = True
        self.followup_until = time.monotonic() + FOLLOWUP_S
        self._ping()
        self.set_state("followup")
        self.emit({"ev": "followup", "seconds": FOLLOWUP_S})

    def _end_listen_now(self) -> None:
        self.armed_until = 0.0
        if self.state == "listening":
            self.set_state("idle")
        if self.s.get("listen_mode") in ("hotkey", "off") and not self.speaking:
            self._capture(False)
            if self.s.get("listen_mode") == "off":
                self.set_state("muted")

    # turns

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            kind, payload, t_end = self.work.get()
            try:
                self._one(kind, payload, t_end)
            except Exception:
                log.exception("turn")
                self.set_state("idle")

    def _one(self, kind: str, payload, t_start: float) -> None:
        """kind: "audio" (to transcribe), "heard" (said during a confirmation, already transcribed) or "text"."""
        asr_s = 0.0
        if kind == "heard":
            text, wake_free, asked = str(payload).strip(), True, True  # mid-conversation: no wake word needed
        elif kind == "audio":
            t_start = t_start if t_start > 0 else time.monotonic()  # 0: no start time known
            wake_free = self._wake_free(at=t_start)
            asked = self.ptt or t_start < self.armed_until or t_start < self.followup_until
            t0 = time.monotonic()
            with self._asr_lock:  # waits for a live update still running (at most one, ~0.1 s on the GPU)
                text = (self.asr.transcribe(payload) or "").strip()
            asr_s = time.monotonic() - t0
            log.info("heard %r (%.2fs)", text, asr_s)
            if self.armed_until:
                self.armed_until = 0.0
        if kind == "audio" and time.monotonic() < self.name_test_until:
            self.name_test_until = 0.0
            self._clear_live()
            self.emit({"ev": "heard", "text": text})
            self._mic_after_turn()
            self.set_state("idle")
            return
        if kind in ("audio", "heard"):
            if not wake_free:
                cmd = strip_wake(text, str(self.s.get("wake_word") or "phoenix"))
                if cmd is None:
                    self._clear_live()
                    if self.state == "listening":
                        self.set_state("idle")
                    if _words(text) and " ".join(_words(text)) not in JUNK:
                        self.emit({"ev": "no_wake"})  # Home hints to say the name first
                    return
                if not _words(cmd):
                    self._called()
                    return
                text = cmd
            elif self.s.get("wake_word"):
                cmd = strip_wake(text, str(self.s.get("wake_word")))
                if cmd is not None and not _words(cmd):
                    self._called()  # only the name: being called, not a request (it once changed the voice)
                    return
                text = cmd or text
            if " ".join(_words(text)) in JUNK:
                self._clear_live()
                if self.state == "listening":
                    self.set_state("followup" if time.monotonic() < self.followup_until else "idle")
                if self.called and wake_free:
                    self.cue("unclear")  # spoken to after being called, but no words came through
                return
            if (self.s.get("listen_mode") == "always" and self.s.get("addressed_check") and not asked
                    and not self.brain.addressed(text)):
                log.info("not for me: %r", text)
                self._clear_live()
                if self.state == "listening":
                    self.set_state("idle")
                return
            if self.s.get("listen_mode") == "hotkey":
                self._capture(False)
        text = str(payload).strip() if kind == "text" else text
        if not text:
            self._clear_live()
            return
        if kind != "text":
            self._emit_live(text, final=True)  # what was heard, shown while the assistant thinks
        self.cancel = threading.Event()
        self._false_barges = 0
        self.called = self.sleep_after = False
        self._think_detail = ""
        self.set_state("thinking")
        self._busy = True
        try:
            turn = self.brain.handle(text, "text" if kind == "text" else "voice", self.cancel)
        finally:
            self._busy = False
        turn["latency"]["asr"] = round(asr_s, 3)
        marked = turn.pop("marks", None)
        if marked is not None:  # "that's wrong": the app saves the earlier request's new status
            self.emit({"ev": "turn_update", "turn": marked})
        self.emit({"ev": "turn", "turn": turn})
        self.say_q.put(("end", turn["id"]))

    # speaking

    def _say(self, text: str, shown: str = "") -> None:
        """Speak ``text``; the pill shows ``shown`` instead when given (a command too long to read out)."""
        self.say_q.put(("say", text, shown or text))

    def _say_wait(self, text: str) -> None:
        """A short phrase while the user waits ("One moment."); the pill goes back to thinking after it."""
        self.say_q.put(("wait", text, text))

    def _speak_loop(self) -> None:
        while not self._stop.is_set():
            item = self.say_q.get()
            if item[0] == "ring":  # a timer: spoken even after a cancelled turn
                try:
                    self._play(item[1], force=True)
                except Exception:
                    log.exception("ringing")
                self.speaking = False
                if self.state == "speaking":
                    self.set_state("muted" if self.s.get("listen_mode") == "off" else "idle")
                continue
            if item[0] == "end":
                self._held()
                self.speaking = False
                if self.cancel.is_set():
                    continue
                if self.sleep_after or self.s.get("listen_mode") == "hotkey":
                    self.sleep_after = False
                    self.followup_until = 0.0
                    self.set_state("idle")
                else:
                    self.followup_until = time.monotonic() + FOLLOWUP_S
                    self.set_state("followup")
                    self.emit({"ev": "followup", "seconds": FOLLOWUP_S})
                continue
            if self.cancel.is_set():
                continue
            try:
                self._play(item[1], item[2] if len(item) > 2 else item[1])
            except Exception as exc:
                log.exception("speaking")
                self.speaking = False
                self.notice("error", f"Couldn't speak the reply: {exc}")
            if item[0] == "wait" and self._busy and self.say_q.empty() and not self.cancel.is_set():
                self.speaking = False
                self.set_state("thinking", self._think_detail)  # the big model is still loading

    def _play(self, text: str, shown: str = "", force: bool = False) -> None:
        if not self.s.get("tts_enabled", True):
            return
        audio = self.tts.generate_speech_audio(text, speed=1.05 * float(self.s.get("tts_speed") or 1.0))
        if audio.size == 0 or (self.cancel.is_set() and not force) or not self._held():
            return
        audio = np.clip(audio * float(self.s.get("tts_volume") or 1.0), -1.0, 1.0)
        sr = self.tts.sample_rate
        hop = int(sr * FRAME_S)
        levels = [float(np.sqrt(np.mean(audio[i:i + hop] ** 2))) for i in range(0, len(audio), hop)]
        while True:
            cut = self._play_once(audio, sr, text, shown, levels)
            if not (cut and self._hold.is_set()) or not self._held():
                return
            log.info("saying the paused sentence again")

    def _play_once(self, audio: np.ndarray, sr: int, text: str, shown: str, levels: list[float]) -> bool:
        """Plays one sentence; True when a barge-in or cancel cut it short."""
        self.speaking = True
        self._saying = text
        # the pill shows the reply as it is spoken, paging long ones over this many seconds
        self.emit({"ev": "saying", "text": shown or text, "seconds": round(len(audio) / sr, 2)})
        self.set_state("speaking")
        done = threading.Event()

        def meter():
            t0 = time.monotonic()
            while not done.wait(0.033):
                i = int((time.monotonic() - t0) / FRAME_S)
                if i >= len(levels):
                    break
                self.emit({"ev": "level", "v": round(levels[i], 4), "src": "tts"})
            self.emit({"ev": "level", "v": 0.0, "src": "tts"})

        threading.Thread(target=meter, daemon=True).start()
        try:
            self.audio.start_speaking(audio, sr, text)
            result = self.audio.measure_percentage_spoken(len(audio), sr)
        finally:
            done.set()
            if not getattr(self.audio, "_aec_ok", False):
                self.quiet_until = time.monotonic() + 0.3
        return bool(isinstance(result, tuple) and result and result[0] is True)

    def _ping(self) -> None:
        if not self.s.get("start_ping", True) or self.audio is None:
            return
        sounds.play("listen", self._sink(), float(self.s.get("tts_volume") or 1.0))

    # tool hooks

    def _confirm(self, request: ConfirmRequest) -> bool:
        """Asks on screen and, when allowed, out loud; the first answer wins, silence is a no."""
        cid = uuid.uuid4().hex
        ev, box = threading.Event(), {"ok": False}
        self._confirms[cid] = (ev, box)
        self.emit({"ev": "confirm", "id": cid, "tool": request.tool_name, "args": request.args,
                   "command": request.command, "detail": request.detail})
        by_voice = (self.s.get("spoken_confirm", True) and self.audio is not None and self.asr is not None
                    and threading.current_thread().name == "Work")
        try:
            if by_voice:
                self._confirm_by_voice(request, ev, box)
            else:
                ev.wait(CONFIRM_S)
        finally:
            self._confirms.pop(cid, None)
        if not ev.is_set() or by_voice:
            self.emit({"ev": "confirm_done", "id": cid, "allowed": box["ok"]})  # closes the dialog
        return box["ok"] and not self.cancel.is_set()

    def _confirm_by_voice(self, request: ConfirmRequest, ev: threading.Event, box: dict) -> None:
        cmd = (request.command or "").strip()
        if request.detail:  # the router was unsure what was meant
            question = request.detail
            self._say(f"{question} Say yes or no.")
        else:
            question = f"Should I run {cmd}?" if cmd and len(cmd) <= 40 else "Should I run this command?"
            self._say(f"{question} Say yes or no.", f"Run this? {cmd} Say yes or no." if cmd else "")
        self.confirming = True
        was_capturing = self.capturing
        self._capture(True)
        deadline = time.monotonic() + CONFIRM_S
        try:
            while not ev.is_set() and time.monotonic() < deadline and not self.cancel.is_set():
                self.followup_until = time.monotonic() + 5  # the answer needs no wake word
                try:
                    kind, payload, _t = self.work.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == "audio":
                    with self._asr_lock:
                        reply = (self.asr.transcribe(payload) or "").strip()
                    self._clear_live()
                else:
                    reply = str(payload)
                answer = self.brain.yes_no(question, reply)
                log.info("confirm reply %r -> %s", reply, answer)
                if answer is not None:
                    box["ok"] = answer
                    ev.set()
                elif kind == "text":
                    self.work.put((kind, payload, _t))  # typed text that isn't an answer waits its turn
                    break
                elif _words(reply) and " ".join(_words(reply)) not in JUNK:
                    # Something else was asked ("Phoenix?", a new request): drop the question, handle that.
                    self.work.put(("heard", reply, time.monotonic()))
                    break
        finally:
            self.confirming = False
            if not was_capturing and self.s.get("listen_mode") in ("hotkey", "off"):
                self._capture(False)

    def _set_voice(self, voice: str, announce: bool = True) -> bool:
        try:
            self.tts.set_voice(self._voice_id(voice))
        except Exception as exc:
            self.notice("error", f"Couldn't switch voice: {exc}")
            return False
        self.s["voice"] = voice
        if announce:
            self.emit({"ev": "voice", "voice": voice})
        return True

    @staticmethod
    def _voice_id(voice) -> str:
        v = str(voice or "supertonic:F1")
        return v.split(":", 1)[1] if v.startswith("supertonic:") else "F1"

    def cue(self, kind: str) -> None:
        """A short sound instead of words; the kinds are in engine/sounds.py."""
        self.emit({"ev": "cue", "kind": kind})
        if self.s.get("tts_enabled", True):
            sounds.play(kind, self._sink(), float(self.s.get("tts_volume") or 1.0))

    def _ring(self, message: str) -> None:
        """A timer is up: a sound, a notification that stays, and the message spoken."""
        text = f"Reminder: {message}." if message else "Your timer is done."
        self.emit({"ev": "notice", "level": "info", "text": text})
        self.cue("timer")
        try:
            from ai_linux.paths import APP_NAME, ICON_NAME
            subprocess.Popen(["notify-send", f"--app-name={APP_NAME}", "-i", ICON_NAME, "-u", "critical",
                              "Reminder" if message else "Timer", message or "Your timer is done."],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            log.warning("notify-send is missing")
        time.sleep(1.2)  # let the sound finish before the voice
        self.say_q.put(("ring", text))

    def _sink(self) -> str | None:
        return getattr(self.audio, "_sink", None) if getattr(self.audio, "_aec_ok", False) else None

    def _sleep(self) -> None:
        self.followup_until = 0.0
        self.armed_until = 0.0
        self.called = False
        self.sleep_after = True  # the end of this turn opens no follow-up window

    @staticmethod
    def _drain(q: queue.Queue) -> list:
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                return out
