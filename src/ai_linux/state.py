"""Assistant state, conversation records and the thread-safe event bus."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum

from gi.repository import GLib, GObject


class AssistantState(str, Enum):
    OFF = "off"  # engine not running
    IDLE = "idle"
    FOLLOWUP = "followup"  # wake-word window open, no wake word needed
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    LOADING = "loading"  # engine or model warming up
    MUTED = "muted"
    ERROR = "error"


# The floating pill shows only in these states (it covers fullscreen apps).
ACTIVE_STATES = frozenset(
    {AssistantState.LISTENING, AssistantState.THINKING, AssistantState.SPEAKING, AssistantState.FOLLOWUP}
)

STATE_LABELS = {
    AssistantState.OFF: "Off",
    AssistantState.IDLE: "Ready",
    AssistantState.FOLLOWUP: "Listening for a follow-up",
    AssistantState.LISTENING: "Listening",
    AssistantState.THINKING: "Thinking",
    AssistantState.SPEAKING: "Speaking",
    AssistantState.LOADING: "Loading",
    AssistantState.MUTED: "Muted",
    AssistantState.ERROR: "Error",
}


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    command: str | None = None  # exact shell command, when there is one
    verdict: str = "ran"  # ran | blocked | denied | needs_confirm | error
    exit_code: int | None = None
    output: str = ""


@dataclass
class Turn:
    heard: str
    reply: str = ""
    route: str = "none"  # small | big | none
    model: str = ""
    confidence: float | None = None
    tools: list[ToolCall] = field(default_factory=list)
    latency: dict[str, float] = field(default_factory=dict)  # asr, route, llm, tts (seconds)
    status: str = "ok"  # ok | failed | cancelled | partial
    source: str = "voice"  # voice | text
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Turn:
        d = dict(d)
        d["tools"] = [ToolCall(**t) for t in d.get("tools", [])]
        return cls(**d)


class Bus(GObject.Object):
    """Engine to UI events. Signals always fire on the GTK main thread.

    Audio levels skip signals: writers set ``level`` from any thread and widgets read it per frame.
    """

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # state, detail
        "turn-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "turn-updated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "notice": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # info|warning|error, text
    }

    def __init__(self):
        super().__init__()
        self.state = AssistantState.OFF
        self.detail = ""
        self.level = 0.0  # 0..1, already perceptually mapped
        self.level_source: str | None = None  # mic | tts
        self.level_ts = 0.0
        self.followup_until: float | None = None  # monotonic deadline of the wake window

    def set_state(self, state: AssistantState | str, detail: str = "") -> None:
        state = AssistantState(state)
        self._main(self._set_state, state, detail)

    def _set_state(self, state: AssistantState, detail: str) -> None:
        if state == self.state and detail == self.detail:
            return
        self.state, self.detail = state, detail
        self.emit("state-changed", state.value, detail)

    def push_level(self, level: float, source: str) -> None:
        self.level = min(1.0, max(0.0, float(level)))
        self.level_source = source
        self.level_ts = time.monotonic()

    def add_turn(self, turn: Turn) -> None:
        self._main(self.emit, "turn-added", turn)

    def update_turn(self, turn: Turn) -> None:
        self._main(self.emit, "turn-updated", turn)

    def notice(self, level: str, text: str) -> None:
        self._main(self.emit, "notice", level, text)

    @staticmethod
    def _main(fn, *args) -> None:
        if threading.current_thread() is threading.main_thread():
            fn(*args)
        else:
            GLib.idle_add(lambda: fn(*args) and False)
