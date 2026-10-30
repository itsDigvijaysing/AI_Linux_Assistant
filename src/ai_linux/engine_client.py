"""The app's side of the engine: starts ``python -m ai_linux.engine`` and maps its events onto the Bus.

The engine has its own Python (speech models need onnxruntime and openvino; the app runs on system Python with
GTK), so it is a child process the app owns: it starts with the app and stops when the app quits.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from gi.repository import GLib

from ai_linux.config import DATA_DIR, DEFAULTS, LOG_DIR, PACKAGE_DIR
from ai_linux.schema import loading
from ai_linux.state import AssistantState, Turn
from ai_linux.ui.bar_model import level_from_rms

log = logging.getLogger(__name__)

PROTOCOL = 1
_STATES = {s.value for s in AssistantState}


def find_python() -> str | None:
    """The engine's interpreter: an override, the installer's venv, then a development conda env."""
    cands = [os.environ.get("AI_LINUX_ENGINE_PYTHON"), str(DATA_DIR / "venv/bin/python")]
    for base in (os.environ.get("CONDA_PREFIX_1"), os.environ.get("CONDA_PREFIX"), "~/miniconda3", "~/anaconda3",
                 "~/miniforge3"):
        if base:
            root = Path(base).expanduser()
            cands += [str(root / "envs/AI_Linux/bin/python")]
    return next((c for c in cands if c and os.access(c, os.X_OK)), None)


def _kill_group(proc: subprocess.Popen) -> None:
    """Ends anything the engine left behind (its own session, so this never reaches the app)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class EngineClient:
    def __init__(self, settings, bus, on_confirm=None):
        self.settings, self.bus, self.on_confirm = settings, bus, on_confirm
        self.python = find_python()
        self.proc: subprocess.Popen | None = None
        self._dialogs: dict[str, object] = {}  # confirm id -> close(), for answers given by voice
        self.models: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stopping = False
        self._restarts = 0
        self._tail: deque[str] = deque(maxlen=20)
        self._handler = settings.connect("changed", self._on_setting)

    @property
    def available(self) -> bool:
        return self.python is not None

    def snapshot(self) -> dict:
        values = {k: self.settings.get(k) for k in DEFAULTS if k != "muted"}
        values["listen_mode"] = self.listen_mode()
        values.update(self.model_values())
        return values

    def model_values(self) -> dict:
        """What the engine runs, from the profile in use: its speed, and the small model only when it is on."""
        small = self.settings.get("small_model") if self.settings.get("small_on") else "none"
        return {"small_model": small, **loading(str(self.settings.get("speed")))}

    def listen_mode(self) -> str:
        return "off" if self.settings.get("muted") else str(self.settings.get("listen_mode"))

    # lifecycle

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.python is None:
            self.bus.set_state(AssistantState.OFF, "Speech engine not installed")
            return
        self._stopping = False
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        err = open(LOG_DIR / "engine.log", "ab")
        env = {**os.environ, "PYTHONPATH": str(PACKAGE_DIR.parent), "PYTHONUNBUFFERED": "1"}
        bindir = Path.home() / ".local/bin"
        if str(bindir) not in env.get("PATH", ""):
            env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
        try:
            self.proc = subprocess.Popen([self.python, "-m", "ai_linux.engine"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                         cwd=str(Path.home()), text=True, bufsize=1, start_new_session=True)
        except OSError as exc:
            err.close()
            self.bus.set_state(AssistantState.ERROR, f"Speech engine failed to start: {exc}")
            return
        self.bus.set_state(AssistantState.LOADING, "Starting the speech engine")
        threading.Thread(target=self._read, args=(self.proc,), name="EngineOut", daemon=True).start()
        threading.Thread(target=self._log, args=(self.proc, err), name="EngineErr", daemon=True).start()
        self._send({"cmd": "settings", "values": self.snapshot()})

    def stop(self) -> None:
        self._stopping = True
        p = self.proc
        if p is None:
            return
        self._send({"cmd": "quit"})
        try:
            p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
        _kill_group(p)
        self.proc = None

    def close(self) -> None:
        self.stop()
        if self._handler:
            self.settings.disconnect(self._handler)
            self._handler = 0

    # commands

    def _send(self, obj: dict) -> None:
        p = self.proc
        if p is None or p.stdin is None:
            return
        with self._lock:
            try:
                p.stdin.write(json.dumps(obj) + "\n")
                p.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                log.warning("engine is gone; dropped %s", obj.get("cmd"))

    def set_listen_mode(self, mode: str) -> None:
        pass  # the settings change already reached the engine

    def listen_now(self) -> None:
        self._send({"cmd": "listen_now"})

    def push_to_talk(self, pressed: bool) -> None:
        self._send({"cmd": "ptt", "pressed": bool(pressed)})

    def cancel(self) -> None:
        self._send({"cmd": "cancel"})

    def new_session(self) -> None:
        self._send({"cmd": "new_session"})

    def submit_text(self, text: str) -> None:
        self._send({"cmd": "text", "text": text})

    def name_test(self, on_heard=None) -> None:
        """Hear one sentence for the name test; on_heard(text) runs on the main thread. None stops it."""
        self.on_heard = on_heard
        self._send({"cmd": "name_test", "on": on_heard is not None})

    def _on_setting(self, _settings, key: str) -> None:
        if key in ("listen_mode", "muted"):
            self._send({"cmd": "settings", "values": {"listen_mode": self.listen_mode()}})
        elif key in ("speed", "small_on", "small_model"):
            self._send({"cmd": "settings", "values": self.model_values()})
        elif key in DEFAULTS:
            self._send({"cmd": "settings", "values": {key: self.settings.get(key)}})

    # events

    def _log(self, proc: subprocess.Popen, sink) -> None:
        with sink:
            for line in proc.stderr:
                self._tail.append(line.rstrip())
                sink.write(line.encode(errors="replace"))
                sink.flush()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                self._event(json.loads(line))
            except Exception:
                log.exception("engine event %r", line[:200])
        code = proc.wait()
        _kill_group(proc)
        if self._stopping or proc is not self.proc:
            return
        last = next((ln for ln in reversed(self._tail) if ln.strip()), "")
        log.error("engine exited with %s: %s", code, last)
        self.proc = None
        if self._restarts < 2:
            self._restarts += 1
            GLib.timeout_add_seconds(2, lambda: self.start() and False)
            self.bus.set_state(AssistantState.LOADING, "Restarting the speech engine")
        else:
            self.bus.set_state(AssistantState.ERROR, f"The speech engine stopped: {last[-160:]}")

    def _event(self, e: dict) -> None:
        ev = e.get("ev")
        if ev == "level":
            v = float(e.get("v") or 0.0)
            self.bus.push_level(level_from_rms(v) if v > 0 else 0.0, e.get("src"))
        elif ev == "state":
            state = e.get("state")
            if state in _STATES:
                if state == "idle":
                    self._restarts = 0
                self.bus.set_state(state, e.get("detail") or "")
        elif ev == "turn":
            self.bus.add_turn(Turn.from_dict(e["turn"]))
        elif ev == "turn_update":
            self.bus.update_turn(Turn.from_dict(e["turn"]))
        elif ev == "partial":
            self.bus.live_text(e.get("text") or "", e.get("stable") or 0, bool(e.get("final")))
        elif ev == "saying":
            self.bus.saying(e.get("text") or "", float(e.get("seconds") or 0))
        elif ev == "knowledge":
            self.bus.knowledge_changed()
        elif ev == "notice":
            self.bus.notice(e.get("level", "info"), e.get("text", ""))
        elif ev == "no_wake":
            self.bus.wake_missed()
        elif ev == "followup":
            self.bus.followup_until = time.monotonic() + float(e.get("seconds") or 0)
        elif ev == "confirm":
            GLib.idle_add(lambda: self._confirm(e) and False)
        elif ev == "voice":
            GLib.idle_add(lambda: self.settings.set("voice", e["voice"]) and False)
        elif ev == "heard":
            cb = getattr(self, "on_heard", None)
            if cb is not None:
                GLib.idle_add(lambda: cb(str(e.get("text") or "")) and False)
        elif ev == "confirm_done":
            GLib.idle_add(lambda: self._close_dialog(e.get("id")) and False)
        elif ev == "model":
            self.models[e.get("name", "")] = e.get("detail", "")
            if str(e.get("detail", "")).startswith("failed"):
                self.bus.notice("warning", f"The small model didn't load: {e['detail']}")
        elif ev == "hello" and e.get("protocol") != PROTOCOL:
            self.bus.notice("error", "The speech engine is a different version than the app. Reinstall it.")

    def _confirm(self, e: dict) -> None:
        cid = str(e.get("id"))

        def respond(allowed: bool) -> None:
            self._dialogs.pop(cid, None)
            self._send({"cmd": "confirm_reply", "id": cid, "allowed": bool(allowed)})
        if self.on_confirm is None:
            respond(False)
            return
        try:
            close = self.on_confirm(e, respond)
            if callable(close):
                self._dialogs[cid] = close
        except Exception:
            log.exception("confirm dialog")
            respond(False)

    def _close_dialog(self, cid) -> None:
        """The engine settled it (a spoken answer, or no answer in time): the dialog goes."""
        close = self._dialogs.pop(str(cid), None)
        if close is not None:
            try:
                close()
            except Exception:
                log.exception("closing the confirm dialog")

