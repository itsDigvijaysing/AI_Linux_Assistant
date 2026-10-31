"""The one place the UI asks the assistant to do something.

The speech engine runs as a child process (engine_client.py). ``--demo`` drives simulated states, levels
and turns instead, so every UI surface can be checked without models.
"""

from __future__ import annotations

import logging
import math
import random
import time

from gi.repository import GLib, GObject

from ai_linux.state import ACTIVE_STATES, AssistantState, Bus, ToolCall, Turn

log = logging.getLogger(__name__)


class Controller(GObject.Object):
    def __init__(self, settings, bus: Bus):
        super().__init__()
        self.settings = settings
        self.bus = bus
        self.engine = None
        self.demo: DemoDriver | None = None
        self.on_confirm = None  # (event dict, respond(bool)) -> None, set by the app
        self.secrets: dict[str, str] = {}  # tokens from the keyring, re-sent whenever the engine starts

    def _ensure_engine(self) -> None:
        if self.engine is None:
            from ai_linux.engine_client import EngineClient
            client = EngineClient(self.settings, self.bus, on_confirm=self._confirm)
            client.secrets = self.secrets
            self.engine = client if client.available else None
            if self.engine is None:
                client.close()

    def playing_sample(self, path: str) -> None:
        if self.engine is not None:
            self.engine.playing_sample(path)

    def cancel_timer(self, tid: str) -> None:
        if self.engine is not None:
            self.engine.cancel_timer(tid)

    def set_secret(self, name: str, value: str) -> None:
        """A token for the engine's tools; an empty value removes it."""
        if value:
            self.secrets[name] = value
        else:
            self.secrets.pop(name, None)
        if self.engine is not None:
            self.engine.send_secret(name, value)

    def _confirm(self, event: dict, respond):
        if self.on_confirm is None:
            respond(False)
            return None
        return self.on_confirm(event, respond)  # may return close() for when it's answered by voice

    @property
    def engine_available(self) -> bool:
        return self.engine is not None or self.demo is not None

    def start(self) -> None:
        if self.demo is None:
            self._ensure_engine()
        if self.engine is not None:
            self.engine.start()
        elif self.demo is None:
            self.bus.set_state(AssistantState.OFF, "Speech engine not installed yet")

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        if self.demo is not None:
            self.demo.stop()
            self.demo = None
        self.bus.set_state(AssistantState.OFF)

    def enable_demo(self) -> None:
        if self.demo is None:
            self.demo = DemoDriver(self.bus)
            self.demo.start()

    def set_listen_mode(self, mode: str) -> None:
        self.settings.set("listen_mode", mode)
        if mode != "off" and self.settings.get("muted"):
            self.settings.set("muted", False)  # picking a way to listen means listening
        self._sync_muted()

    def mute(self, on: bool) -> None:
        """Mute is separate from the profiles' listen mode, so plugging in the charger doesn't unmute."""
        self.settings.set("muted", bool(on))
        if not on and self.settings.get("listen_mode") == "off":
            self.settings.set("listen_mode", "wake")
        self._sync_muted()

    def is_muted(self) -> bool:
        return bool(self.settings.get("muted")) or self.settings.get("listen_mode") == "off"

    def _sync_muted(self) -> None:
        if self.is_muted():
            self.bus.set_state(AssistantState.MUTED)
        elif self.bus.state == AssistantState.MUTED:
            self.bus.set_state(AssistantState.IDLE)

    def toggle_listening(self) -> None:
        """Hotkey / tray: start a turn now, or cancel the one in progress."""
        if self.bus.state in ACTIVE_STATES:
            self.cancel()
        elif self.engine is not None:
            self.engine.listen_now()
        elif self.demo is not None:
            self.demo.run_turn()

    def push_to_talk(self, pressed: bool) -> None:
        if self.engine is not None:
            self.engine.push_to_talk(pressed)
        elif pressed:
            self.toggle_listening()

    def new_session(self) -> None:
        """The model starts fresh and Home empties; History keeps every turn."""
        if self.engine is not None:
            self.engine.new_session()
        self.bus.new_session()

    def delete_session(self, history, turns) -> None:
        """Home's Delete This Session: a New Session whose turns leave History too."""
        for t in turns:
            history.delete(t.id)
        self.new_session()

    def restore_session(self, history, turns, start: float) -> None:
        """Undo of delete_session: the turns return to History and Home; the model still starts fresh."""
        for t in turns:
            history.add(t)
        self.bus.restore_session(start)

    def cancel(self) -> None:
        if self.engine is not None:
            self.engine.cancel()
        if self.demo is not None:
            self.demo.cancel()

    def name_test(self, on_heard=None) -> bool:
        """Hear one sentence for the name test (on_heard gets the text); None stops. False without the engine."""
        if self.engine is None:
            return False
        self.engine.name_test(on_heard)
        return True

    def submit_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.engine is not None:
            self.engine.submit_text(text)
        elif self.demo is not None:
            self.demo.run_turn(text=text)
        else:
            self.bus.notice("warning", "The speech engine is not installed yet")


_DEMO_TURNS = [
    ("open files", "Opening Files.", "small", "jiwo-0.8b", 0.97,
     [ToolCall("open_app_or_link", {"target": "files"}, "setsid -f nautilus", "ran", 0)]),
    ("make it a bit louder", "Volume up to 60 percent.", "small", "jiwo-0.8b", 0.93,
     [ToolCall("set_volume", {"level": 60}, "wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.6", "ran", 0)]),
    ("what's using all my memory right now", "Chrome is using 3.1 GB, then VS Code with 1.4 GB.", "big",
     "qwen3.5:4b", 0.71, [ToolCall("run_command", {}, "ps -eo comm,rss --sort=-rss | head -5", "ran", 0)]),
    ("delete everything in my home folder", "I won't do that, it would erase your files.", "big",
     "qwen3.5:4b", 0.88, [ToolCall("run_command", {}, "rm -rf ~/*", "blocked", None, "catastrophic delete")]),
    ("switch to a female voice", "Done, how does this sound?", "small", "jiwo-0.8b", 0.95,
     [ToolCall("set_voice", {"voice": "supertonic:F1"}, None, "ran", 0)]),
]


class DemoDriver:
    """Cycles idle, listening, thinking, speaking and follow-up with synthetic audio levels."""

    def __init__(self, bus: Bus):
        self.bus = bus
        self._timers: list[int] = []
        self._level_id = 0
        self._i = 0
        self._seed = random.Random(7)

    def start(self) -> None:
        self.bus.set_state(AssistantState.IDLE)
        self._after(1500, self.run_turn)

    def stop(self) -> None:
        self.cancel()

    def cancel(self) -> None:
        for t in self._timers:
            GLib.source_remove(t)
        self._timers.clear()
        self._levels(None)
        self.bus.set_state(AssistantState.IDLE)

    def run_turn(self, text: str | None = None) -> None:
        self.cancel()
        heard, reply, route, model, conf, tools = _DEMO_TURNS[self._i % len(_DEMO_TURNS)]
        self._i += 1
        if text:
            heard, route, model, conf, tools = text, "big", "qwen3.5:4b", 0.8, []
            reply = f"(demo) You said: {text}"
        t0 = time.monotonic()
        listen_ms = 0 if text else 2600
        if not text:
            self.bus.set_state(AssistantState.LISTENING)
            self._levels("mic")

        def think():
            self._levels(None)
            self.bus.set_state(AssistantState.THINKING)

        def speak():
            self.bus.set_state(AssistantState.SPEAKING)
            self._levels("tts")

        def done():
            self._levels(None)
            lat = {"asr": 0.21, "route": 0.18 if route == "small" else 0.2, "llm": 0.0 if route == "small" else 1.1,
                   "tts": 0.32}
            self.bus.add_turn(Turn(heard=heard, reply=reply, route=route, model=model, confidence=conf,
                                   tools=tools, latency=lat, source="text" if text else "voice"))
            self.bus.followup_until = time.monotonic() + 6
            self.bus.set_state(AssistantState.FOLLOWUP)
            self._after(6000, lambda: self.bus.set_state(AssistantState.IDLE))
            self._after(9000, self.run_turn)

        self._after(listen_ms, think)
        self._after(listen_ms + (1600 if route == "big" else 700), speak)
        self._after(listen_ms + (1600 if route == "big" else 700) + 2400, done)
        log.debug("demo turn %r started at %.2f", heard, t0)

    def _after(self, ms: int, fn) -> None:
        tid = 0

        def fire():
            if tid in self._timers:
                self._timers.remove(tid)
            fn()
            return False

        tid = GLib.timeout_add(max(1, ms), fire)
        self._timers.append(tid)

    def _levels(self, source: str | None) -> None:
        if self._level_id:
            GLib.source_remove(self._level_id)
            self._level_id = 0
        if source is None:
            self.bus.push_level(0.0, None)
            return
        start = time.monotonic()

        def tick():
            t = time.monotonic() - start
            # syllable-rate envelope (about 4 Hz) with word gaps, like real speech
            env = max(0.0, math.sin(2 * math.pi * 4.1 * t)) ** 0.6
            gap = 0.25 if (t % 1.3) > 1.0 else 1.0
            self.bus.push_level(min(1.0, (0.15 + 0.8 * env) * gap * (0.85 + 0.3 * self._seed.random())), source)
            return True

        self._level_id = GLib.timeout_add(32, tick)
