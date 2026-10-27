"""Live microphone level for the Voice page and the onboarding mic step.

Runs ``pw-record`` only while a meter is started; stop() always ends the subprocess. Pure stdlib
(array + math) because numpy is not guaranteed on the system Python.
"""

from __future__ import annotations

import array
import atexit
import json
import logging
import math
import subprocess
import threading
import weakref

from ai_linux.ui.bar_model import level_from_rms

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 512  # samples per RMS window, about 31 windows per second
_SOURCE_CLASSES = ("Audio/Source", "Audio/Source/Virtual")
_live: weakref.WeakSet[MicMeter] = weakref.WeakSet()


def _sumsq(samples: array.array) -> float:
    try:
        return math.sumprod(samples, samples)  # C speed on 3.12+
    except AttributeError:
        return float(sum(s * s for s in samples))


def rms_s16(raw: bytes) -> float:
    """RMS of little-endian s16 PCM as a 0..1 float."""
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if not samples:
        return 0.0
    return math.sqrt(_sumsq(samples) / len(samples)) / 32768.0


class MicMeter:
    """``level`` (0..1, perceptual) updates about 31 times a second from any thread; read it per frame."""

    def __init__(self, device: str | None = None):
        self.device = device or None  # PipeWire node.name, None = system default
        self.level = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running and not self._stop.is_set():
            return
        # a stopping worker owns its own event and process, so a restart never waits for it
        self._stop = threading.Event()
        self.error, self.level = None, 0.0
        # spawn inside the worker so the GTK main thread never waits on fork/exec
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="MicMeter", daemon=True)
        self._thread.start()
        _live.add(self)

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        if wait:
            self._join_old()
        self.level = 0.0

    def set_device(self, device: str | None) -> None:
        device = device or None
        if device == self.device:
            return
        was_running = self.running and not self._stop.is_set()
        self.stop()
        self.device = device
        if was_running:
            self.start()

    def _join_old(self) -> None:
        old = self._thread
        if old is not None and old.is_alive() and old is not threading.current_thread():
            old.join(timeout=1.5)

    def _command(self) -> list[str]:
        cmd = ["pw-record", "--container", "raw", "--rate", str(RATE), "--channels", "1", "--format", "s16",
               "--media-category", "Capture", "--media-role", "Communication"]
        if self.device:
            cmd += ["--target", self.device]
        return cmd + ["-"]

    def _run(self, stop: threading.Event) -> None:
        with self._lock:
            if stop.is_set():
                return
            try:
                # SIGPIPE is restored in the child, so pw-record also dies if this process does
                proc = subprocess.Popen(self._command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, bufsize=0)
            except OSError as exc:
                self.error = "PipeWire recorder (pw-record) is not installed" if isinstance(
                    exc, FileNotFoundError) else str(exc)
                log.warning("mic meter: %s", exc)
                return
            self._proc = proc
        nbytes = FRAME * 2
        buf = b""
        try:
            while not stop.is_set():
                chunk = proc.stdout.read(nbytes)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= nbytes:
                    raw, buf = buf[:nbytes], buf[nbytes:]
                    self.level = level_from_rms(rms_s16(raw))
        except Exception:  # a broken pipe or decode error must only stop the meter
            log.exception("mic meter reader")
        finally:
            self._reap(proc, stop)

    def _reap(self, proc: subprocess.Popen, stop: threading.Event) -> None:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1.0)
        err = b""
        try:
            err = proc.stderr.read() or b""
        except Exception:
            pass
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        if not stop.is_set() and proc.returncode not in (0, None):
            msg = err.decode(errors="replace").strip().splitlines()
            self.error = msg[-1] if msg else f"pw-record exited with code {proc.returncode}"
            log.warning("mic meter stopped: %s", self.error)
        with self._lock:
            if self._proc is proc:
                self._proc = None
        self.level = 0.0


def _kill_all() -> None:
    for meter in list(_live):
        meter.stop(wait=True)


atexit.register(_kill_all)


def _pw_dump(timeout: float = 5.0) -> list:
    out = subprocess.run(["pw-dump"], capture_output=True, timeout=timeout, check=False).stdout
    data = json.loads(out or b"[]")
    return data if isinstance(data, list) else []


def _default_source(objs: list) -> str | None:
    for o in objs:
        props = (o.get("props") or {})
        if o.get("type") != "PipeWire:Interface:Metadata" or props.get("metadata.name") != "default":
            continue
        for entry in o.get("metadata") or []:
            if entry.get("key") == "default.audio.source":
                value = entry.get("value")
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        return value
                return value.get("name") if isinstance(value, dict) else None
    return None


def list_sources_with_default() -> tuple[list[tuple[str, str]], str | None]:
    """Blocking (runs pw-dump): call from a worker thread. Returns (sources, default node name)."""
    try:
        objs = _pw_dump()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.warning("listing microphones failed: %s", exc)
        return [], None
    sources = []
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        props = (o.get("info") or {}).get("props") or {}
        name = props.get("node.name") or ""
        if props.get("media.class") not in _SOURCE_CLASSES or not name:
            continue
        if name.endswith(".monitor") or props.get("stream.monitor") or props.get("device.class") == "monitor":
            continue
        sources.append((name, props.get("node.description") or props.get("node.nick") or name))
    return sources, _default_source(objs)


def list_sources() -> list[tuple[str, str]]:
    """[(node_name, description)] of microphones, monitors skipped. Blocking: call off the main thread."""
    return list_sources_with_default()[0]


def list_sources_async(callback) -> None:
    """Run list_sources_with_default in a thread and call ``callback(sources, default)`` on the main loop."""
    from gi.repository import GLib

    def work():
        result = list_sources_with_default()
        GLib.idle_add(lambda: callback(*result) and False)

    threading.Thread(target=work, name="MicSources", daemon=True).start()
