"""The engine: mic -> VAD -> wake word -> Parakeet -> brain -> SuperTonic -> speaker.

Runs as a child of the app (``python -m ai_linux.engine``) and talks over stdin/stdout, one JSON object per
line: commands in, events out (see PROTOCOL in __main__). Logs go to stderr.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from ai_linux.engine import sounds
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
BARGE_FRAMES = 6  # voiced frames that interrupt the assistant (~0.2 s)
FOLLOWUP_S = 10.0  # after a reply, follow-ups need no wake word
CONFIRM_S = 55.0  # a confirmation not answered by then is a no (the app's dialog gives up at the same time)
LISTEN_NOW_S = 8.0  # hotkey: how long to wait for speech to start
NAME_TEST_S = 20.0  # a name-test sentence must be heard within this
JUNK = {"", "yeah", "yes", "mm", "hmm", "uh", "um", "ah", "oh", "okay", "ok", "you", "bye", "the", "a", "and", "so"}


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
        self.quiet_until = 0.0  # half-duplex: ignore the mic until our own voice has died away
        self._confirms: dict[str, tuple[threading.Event, dict]] = {}
        self.confirming = False
        self.mcp = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # settings and helpers

    def setting(self, key: str):
        return self.s.get(key)

    def set_state(self, state: str, detail: str = "") -> None:
        if state != self.state or detail:
            self.state = state
            self.emit({"ev": "state", "state": state, "detail": detail})

    def notice(self, level: str, text: str) -> None:
        self.emit({"ev": "notice", "level": level, "text": text})

    # startup

    def start(self) -> None:
        threading.Thread(target=self._load, name="EngineLoad", daemon=True).start()

    def _load(self) -> None:
        from ai_linux.engine.asr import get_transcriber
        from ai_linux.engine.audio import PipeWireAudioIO
        from ai_linux.engine.brain import Brain
        from ai_linux.engine.tools import Env
        from ai_linux.engine.tts import SpeechSynthesizer

        self.set_state("loading", "Loading speech models")
        self.gate = ActionGate(armed=bool(self.s.get("actions_armed")), confirm_globs=self._confirm_globs())
        env = Env(Executor(self.gate), confirm=self._confirm, set_voice=self._set_voice, sleep=self._sleep,
                  cue=self.cue)
        try:
            self.tts = SpeechSynthesizer(self._voice_id(self.s.get("voice")))
            self.asr = get_transcriber(str(self.s.get("stt_engine") or "parakeet-tdt"))
            self.audio = PipeWireAudioIO()
        except Exception as exc:
            log.exception("loading speech models")
            self.set_state("error", f"Speech models failed to load: {exc}")
            return
        if not getattr(self.audio, "_aec_ok", False):
            self.notice("warning", "Echo cancelling isn't available, so you can't talk over the assistant.")
        self.brain = Brain(self.setting, env, speak=self._say,
                           loaded=lambda what, d: self.emit({"ev": "model", "name": what, "detail": d}))
        for target, name in ((self._listen_loop, "Listen"), (self._work_loop, "Work"), (self._speak_loop, "Speak")):
            threading.Thread(target=target, name=name, daemon=True).start()
        self.apply_listen_mode()
        threading.Thread(target=self.brain.warm, name="Warm", daemon=True).start()
        threading.Thread(target=self._load_mcp, name="MCP", daemon=True).start()

    def _load_mcp(self) -> None:
        from ai_linux.engine.mcp_client import McpTools
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
        if self.mcp is not None:
            self.mcp.close()
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
        if self.s.get("stt_engine") != old.get("stt_engine") and self.asr is not None:
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
        if changed & (big | {"small_model", "small_device", "big_model_policy"}):
            if changed & {"small_model", "small_device"}:
                self.brain.drop_small()
            threading.Thread(target=self.brain.warm, name="Warm", daemon=True).start()
        if "mcp_servers" in changed:
            threading.Thread(target=self._load_mcp, name="MCP", daemon=True).start()

    def _confirm_globs(self) -> list[str]:
        return ["shell.run_command"] if self.s.get("confirm_shell", True) else []

    def _reload_asr(self) -> None:
        from ai_linux.engine.asr import get_transcriber
        try:
            self.asr = get_transcriber(str(self.s.get("stt_engine")))
        except Exception as exc:
            self.notice("error", f"Couldn't switch speech recognition: {exc}")

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

    def listen_now(self) -> None:
        """Hotkey or tray: take the next thing said, no wake word."""
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

    def cancel_turn(self) -> None:
        self.cancel.set()
        self._drain(self.say_q)
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
            except Exception as exc:
                log.exception("microphone")
                self.notice("error", f"The microphone isn't available: {exc}")

    def _wake_free(self) -> bool:
        now = time.monotonic()
        return (self.s.get("listen_mode") == "always" or self.ptt or now < self.armed_until
                or now < self.followup_until)

    def _listen_loop(self) -> None:
        pre = collections.deque(maxlen=PREROLL)
        rec: list[np.ndarray] = []
        voiced = silent = barge = 0
        recording = ptt_rec = False
        last_level = 0.0
        while not self._stop.is_set():
            if not self.capturing or self.audio is None:
                time.sleep(0.05)
                pre.clear()
                rec, recording, voiced, silent = [], False, 0, 0
                continue
            try:
                frame, vad = self.audio.get_sample_queue().get(timeout=0.2)
            except queue.Empty:
                if self.armed_until and time.monotonic() > self.armed_until and not recording and not self.ptt:
                    self._end_listen_now()
                continue
            aec = getattr(self.audio, "_aec_ok", False)
            if self.speaking:
                if not (aec and self.s.get("barge_in", True)):
                    continue  # half-duplex: the mic would hear our own voice
                barge = barge + 1 if vad else 0
                if barge < BARGE_FRAMES:
                    pre.append(frame)
                    continue
                log.info("barge-in")
                if self.confirming:  # the answer to our question, not an interruption
                    self._drain(self.say_q)
                    self.audio.stop_speaking()
                    self.speaking = False
                else:
                    self.cancel_turn()
                    self.followup_until = time.monotonic() + FOLLOWUP_S
                barge = 0
            elif time.monotonic() < self.quiet_until:
                continue
            if self.ptt:
                vad = True  # hold-to-talk records everything until release
            if not recording:
                pre.append(frame)
                if vad:
                    recording, rec, voiced, silent = True, list(pre), 1, 0
                    ptt_rec = self.ptt
                    if self._wake_free():
                        self.set_state("listening")
                continue
            rec.append(frame)
            voiced, silent = (voiced + 1, 0) if vad else (voiced, silent + 1)
            if self.state == "listening" and time.monotonic() - last_level > 0.03:
                last_level = time.monotonic()
                self.emit({"ev": "level", "v": round(float(np.sqrt(np.mean(frame ** 2))), 4), "src": "mic"})
            done = (silent >= END_SILENCE and not self.ptt) or len(rec) >= MAX_FRAMES or (ptt_rec and not self.ptt)
            if done:
                audio = np.concatenate(rec)
                recording, rec, pre = False, [], collections.deque(maxlen=PREROLL)
                if voiced >= MIN_SPEECH:
                    self.work.put(("audio", audio, time.monotonic()))
                elif self.state == "listening" and not self._wake_free():
                    self.set_state("idle")
                self.emit({"ev": "level", "v": 0.0, "src": "mic"})

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

    def _one(self, kind: str, payload, t_end: float) -> None:
        asr_s = 0.0
        if kind == "audio":
            wake_free = self._wake_free()
            now = time.monotonic()
            asked = self.ptt or now < self.armed_until or now < self.followup_until
            t0 = time.monotonic()
            text = (self.asr.transcribe(payload) or "").strip()
            asr_s = time.monotonic() - t0
            log.info("heard %r (%.2fs)", text, asr_s)
            if self.armed_until:
                self.armed_until = 0.0
            if time.monotonic() < self.name_test_until:
                self.name_test_until = 0.0
                self.emit({"ev": "heard", "text": text})
                self._mic_after_turn()
                self.set_state("idle")
                return
            if not wake_free:
                cmd = strip_wake(text, str(self.s.get("wake_word") or "phoenix"))
                if cmd is None:
                    if self.state == "listening":
                        self.set_state("idle")
                    return
                if not _words(cmd):
                    self.followup_until = time.monotonic() + FOLLOWUP_S
                    self._ping()
                    self.set_state("followup")
                    self.emit({"ev": "followup", "seconds": FOLLOWUP_S})
                    return
                text = cmd
            elif self.s.get("wake_word"):
                text = strip_wake(text, str(self.s.get("wake_word"))) or text
            if " ".join(_words(text)) in JUNK:
                if self.state == "listening":
                    self.set_state("followup" if time.monotonic() < self.followup_until else "idle")
                return
            if (self.s.get("listen_mode") == "always" and self.s.get("addressed_check") and not asked
                    and not self.brain.addressed(text)):
                log.info("not for me: %r", text)
                if self.state == "listening":
                    self.set_state("idle")
                return
            if self.s.get("listen_mode") == "hotkey":
                self._capture(False)
        text = str(payload).strip() if kind == "text" else text
        if not text:
            return
        self.cancel = threading.Event()
        self.set_state("thinking")
        turn = self.brain.handle(text, "voice" if kind == "audio" else "text", self.cancel)
        turn["latency"]["asr"] = round(asr_s, 3)
        self.emit({"ev": "turn", "turn": turn})
        self.say_q.put(("end", turn["id"]))

    # speaking

    def _say(self, text: str) -> None:
        self.say_q.put(("say", text))

    def _speak_loop(self) -> None:
        while not self._stop.is_set():
            item = self.say_q.get()
            if item[0] == "end":
                self.speaking = False
                if self.cancel.is_set():
                    continue
                if self.s.get("listen_mode") == "hotkey":
                    self.set_state("idle")
                else:
                    self.followup_until = time.monotonic() + FOLLOWUP_S
                    self.set_state("followup")
                    self.emit({"ev": "followup", "seconds": FOLLOWUP_S})
                continue
            if self.cancel.is_set():
                continue
            try:
                self._play(item[1])
            except Exception:
                log.exception("speaking")

    def _play(self, text: str) -> None:
        if not self.s.get("tts_enabled", True):
            return
        audio = self.tts.generate_speech_audio(text, speed=1.05 * float(self.s.get("tts_speed") or 1.0))
        if audio.size == 0 or self.cancel.is_set():
            return
        audio = np.clip(audio * float(self.s.get("tts_volume") or 1.0), -1.0, 1.0)
        sr = self.tts.sample_rate
        hop = int(sr * FRAME_S)
        levels = [float(np.sqrt(np.mean(audio[i:i + hop] ** 2))) for i in range(0, len(audio), hop)]
        self.speaking = True
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
            self.audio.measure_percentage_spoken(len(audio), sr)
        finally:
            done.set()
            if not getattr(self.audio, "_aec_ok", False):
                self.quiet_until = time.monotonic() + 0.3

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
                   "command": request.command})
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
        question = f"Should I run {cmd}?" if cmd and len(cmd) <= 40 else "Should I run this command?"
        self._say(f"{question} Say yes or no.")
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
                reply = (self.asr.transcribe(payload) or "").strip() if kind == "audio" else str(payload)
                answer = self.brain.yes_no(question, reply)
                log.info("confirm reply %r -> %s", reply, answer)
                if answer is not None:
                    box["ok"] = answer
                    ev.set()
                elif kind == "text":
                    self.work.put((kind, payload, _t))  # typed text that isn't an answer waits its turn
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
        v = str(voice or "supertonic:M1")
        return v.split(":", 1)[1] if v.startswith("supertonic:") else "M1"

    def cue(self, kind: str) -> None:
        """A short sound instead of words: ok, fail, or big (the big model is waking up)."""
        self.emit({"ev": "cue", "kind": kind})
        if kind == "big":
            self.set_state("thinking", "The big model is answering")
        if self.s.get("tts_enabled", True):
            sounds.play(kind, self._sink(), float(self.s.get("tts_volume") or 1.0))

    def _sink(self) -> str | None:
        return getattr(self.audio, "_sink", None) if getattr(self.audio, "_aec_ok", False) else None

    def _sleep(self) -> None:
        self.followup_until = 0.0
        self.armed_until = 0.0

    @staticmethod
    def _drain(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return
