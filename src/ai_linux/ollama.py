"""Ollama runtime: detect, start/stop as our own user unit, list, pull, delete and one-token decisions.

Stdlib only. Network and subprocess work runs in daemon threads; callbacks and the
``status-changed`` signal always arrive on the GTK main thread.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from gi.repository import GLib, GObject

from ai_linux import downloads
from ai_linux.config import CACHE_DIR, DATA_DIR, LOG_DIR, STATE_DIR

log = logging.getLogger(__name__)

UNIT = "ai-linux-ollama"
DEFAULT_URL = "http://127.0.0.1:11434"
MANAGED_DIR = DATA_DIR / "ollama"  # our own installs: <version>/bin/ollama
PID_FILE = STATE_DIR / "ollama.pid"  # only for the no-systemd fallback
START_TIMEOUT_S = 45  # first start on a hybrid laptop waits ~25 s for GPU discovery
STOP_TIMEOUT_S = 20

# Pinned official release, read from the GitHub release API on 2026-10-27.
OLLAMA_PIN = "0.40.11"
OLLAMA_PIN_TESTED = False  # not yet installed and run through install() on a real machine
OLLAMA_ASSETS = {  # arch -> (asset, bytes, sha256)
    "amd64": ("ollama-linux-amd64.tar.zst", 1508659610,
              "7bedab90dd122661be78798e9f0edf37fccf0cccf2699d2c58ea1a3afdc04b6d"),
    "arm64": ("ollama-linux-arm64.tar.zst", 1564235434,
              "77b278837b9e62c172f77d703675be4c52c8054e9075639031a9a6d5a53f1924"),
}
RELEASE_URL = "https://github.com/ollama/ollama/releases/download/v{version}/{asset}"
_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ROUTER_KEEP_ALIVE = "30m"  # the router runs every utterance; ollama_keep_alive is the big model's setting
_LETTER_TOKEN = re.compile(r"^\(?([A-Z])[).:]?$")
_VERSION = re.compile(r"(\d+\.\d+\.\d+[\w.+-]*)")

# Local servers must never go through a desktop proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaError(Exception):
    pass


# ---------------------------------------------------------------- pure helpers (unit-tested)

def install_asset(machine: str | None = None) -> tuple[str, int, str] | None:
    arch = _ARCH.get((machine or platform.machine()).lower())
    return OLLAMA_ASSETS.get(arch) if arch else None


def normalize_tag(tag: str) -> str:
    """'qwen3' -> 'qwen3:latest'; a registry port ('host:5000/m') is not a tag."""
    tag = tag.strip()
    return tag if ":" in tag.rsplit("/", 1)[-1] else f"{tag}:latest"


def parse_version(text: str) -> str | None:
    found = _VERSION.findall(text or "")
    return found[-1] if found else None


def split_url(url: str) -> tuple[str, int, bool]:
    """Return (host, port, https) for an Ollama base URL."""
    parts = urllib.parse.urlsplit(url if "://" in url else f"http://{url}")
    https = parts.scheme == "https"
    return parts.hostname or "127.0.0.1", parts.port or (443 if https else 11434), https


def is_local_url(url: str) -> bool:
    return split_url(url)[0] in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env).expanduser() if env else Path.home() / ".ollama" / "models"


def scan_manifests(root: Path | None = None) -> list[dict]:
    """Models pulled to disk, read from Ollama's manifest tree so the list works while it is stopped."""
    base = (root or models_dir()) / "manifests"
    found = []
    try:
        paths = [p for p in base.rglob("*") if p.is_file()]
    except OSError:
        return found
    for path in paths:
        try:
            parts = path.relative_to(base).parts  # registry, namespace..., model, tag
            if len(parts) < 4:
                continue
            registry, *names, tag = parts
            if registry == "registry.ollama.ai" and names[0] == "library":
                name = "/".join(names[1:])
            elif registry == "registry.ollama.ai":
                name = "/".join(names)
            else:
                name = "/".join((registry, *names))
            data = json.loads(path.read_text())
            size = sum(int(layer.get("size", 0)) for layer in data.get("layers", []))
            found.append({
                "name": f"{name}:{tag}", "size": size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(path.stat().st_mtime)),
                "details": {},
            })
        except (OSError, ValueError, AttributeError, IndexError):
            log.debug("skipping manifest %s", path)
    return sorted(found, key=lambda m: m["name"])


def letter_probs(logprobs: list | None, n_options: int) -> dict[str, float]:
    """Raw probability per option letter from the first generated token's top_logprobs."""
    allowed = LETTERS[:n_options]
    probs = {letter: 0.0 for letter in allowed}
    if not logprobs:
        return probs
    first = logprobs[0] or {}
    candidates = list(first.get("top_logprobs") or [])
    if not candidates and "token" in first:
        candidates = [first]
    for cand in candidates:
        match = _LETTER_TOKEN.match(str(cand.get("token", "")).strip())
        if match and match.group(1) in allowed:
            try:
                probs[match.group(1)] += math.exp(float(cand.get("logprob", -math.inf)))
            except (TypeError, ValueError, OverflowError):
                continue
    return probs


def build_decision_prompt(system: str, options: list[str]) -> str:
    lines = [f"{LETTERS[i]}. {text}" for i, text in enumerate(options)]
    return f"{system.rstrip()}\n\nOptions:\n" + "\n".join(lines) + "\n\nReply with the letter of one option only."


def find_binary() -> str | None:
    """Our managed install first (pinned build), then the usual places."""
    try:
        managed = sorted(MANAGED_DIR.glob("*/bin/ollama"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        managed = []
    candidates = [*managed, shutil.which("ollama"), Path.home() / ".local/bin/ollama",
                  "/usr/local/bin/ollama", "/usr/bin/ollama"]
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return str(cand)
    return None


_version_cache: dict[tuple[str, int], str | None] = {}


def binary_version(binary: str) -> str | None:
    """`ollama --version`, cached per file so polling never re-runs it."""
    try:
        key = (binary, os.stat(binary).st_mtime_ns)
    except OSError:
        return None
    if key not in _version_cache:
        try:
            # Point the client at a closed port so it answers instantly without a server.
            env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:9"}
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5, env=env)
            _version_cache[key] = parse_version(out.stdout + out.stderr)
        except (OSError, subprocess.SubprocessError):
            _version_cache[key] = None
    return _version_cache[key]


# ---------------------------------------------------------------- HTTP

def _request(method: str, url: str, body: dict | None = None, timeout: float = 3.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise OllamaError(_error_text(exc.read(), f"HTTP {exc.code}")) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OllamaError(str(getattr(exc, "reason", exc))) from exc
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise OllamaError("Ollama sent a reply that is not JSON") from exc


def _error_text(raw: bytes, fallback: str) -> str:
    try:
        return json.loads(raw).get("error") or fallback
    except (ValueError, AttributeError):
        return raw.decode(errors="replace").strip()[:200] or fallback


def _run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("%s failed: %s", cmd[0], exc)
        return None


# ---------------------------------------------------------------- handles

class Handle:
    """A running pull or install; ``cancel()`` is safe from any thread."""

    def __init__(self):
        self.event = threading.Event()
        self._conn: http.client.HTTPConnection | None = None

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def cancel(self) -> None:
        self.event.set()
        conn = self._conn
        if conn is not None and conn.sock is not None:
            try:  # unblocks a read waiting on a quiet stream (e.g. during digest checks)
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# ---------------------------------------------------------------- manager

def _empty_status(url: str) -> dict:
    return {
        "installed": False, "binary": None, "version": None, "running": False, "owner": None,
        "url": url, "models": [], "models_source": None, "loaded": [], "error": None,
        "busy": None, "local": is_local_url(url), "managed": False, "checked": False,
    }


class OllamaManager(GObject.Object):
    """Owns the Ollama lifecycle for the app. ``status`` is only touched on the main thread."""

    __gsignals__ = {"status-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.settings = getattr(ctx, "settings", None)
        self.status = _empty_status(self.url)
        self._busy: str | None = None
        self._error: str | None = None
        self._refreshing = False
        self._refresh_again = False
        self._refresh_callbacks: list[Callable] = []
        self._next_callbacks: list[Callable] = []  # asked for mid-refresh: need the next snapshot
        self._pollers = 0
        self._poll_source = 0
        self._proc: subprocess.Popen | None = None
        GLib.idle_add(self._initial_refresh)

    # ---- settings and dispatch

    @property
    def url(self) -> str:
        url = self.settings.get("ollama_url") if self.settings is not None else DEFAULT_URL
        return (url or DEFAULT_URL).rstrip("/")

    def _keep_alive(self) -> str:
        value = self.settings.get("ollama_keep_alive") if self.settings is not None else "10m"
        return str(value or "10m")

    @staticmethod
    def _main(fn: Callable | None, *args) -> None:
        if fn is not None:
            GLib.idle_add(lambda: (fn(*args), False)[1])

    def _spawn(self, name: str, target: Callable, *args) -> None:
        threading.Thread(target=target, args=args, name=f"ollama {name}", daemon=True).start()

    def _set_busy(self, busy: str | None, error: str | None = None, clear_error: bool = False) -> None:
        self._busy = busy
        if clear_error:
            self._error = None
        if error:
            self._error = error
        self._publish({**self.status, "busy": self._busy, "error": self._error})

    def _publish(self, status: dict) -> None:
        if status != self.status:
            self.status = status
            self.emit("status-changed")

    # ---- refresh and polling

    def _initial_refresh(self) -> bool:
        self.refresh()
        return False

    def refresh(self, on_done: Callable[[dict], None] | None = None) -> None:
        if self._refreshing:
            self._refresh_again = True
            if on_done is not None:
                self._next_callbacks.append(on_done)
            return
        if on_done is not None:
            self._refresh_callbacks.append(on_done)
        self._refreshing = True
        self._spawn("refresh", self._refresh_worker, self.url)

    def clear_error(self) -> None:
        self._error = None
        self._publish({**self.status, "error": None})

    def _refresh_worker(self, url: str) -> None:
        try:
            snap = self._probe(url)
        except Exception as exc:  # a probe bug must not freeze the status forever
            log.exception("ollama probe failed")
            snap = {**_empty_status(url), "checked": True, "error": str(exc)}
        self._main(self._refresh_finished, snap)

    def _refresh_finished(self, snap: dict) -> None:
        self._refreshing = False
        status = {**snap, "busy": self._busy, "error": self._error or snap.get("error")}
        self._publish(status)
        callbacks, self._refresh_callbacks = self._refresh_callbacks, []
        for cb in callbacks:
            try:
                cb(self.status)
            except Exception:
                log.exception("refresh callback")
        if self._refresh_again:
            self._refresh_again = False
            self._refresh_callbacks, self._next_callbacks = self._next_callbacks, []
            self._refreshing = True
            self._spawn("refresh", self._refresh_worker, self.url)

    def _probe(self, url: str) -> dict:
        snap = _empty_status(url)
        snap["checked"] = True
        binary = find_binary()
        snap["binary"] = binary
        snap["managed"] = bool(binary and Path(binary).is_relative_to(MANAGED_DIR))
        try:
            snap["version"] = _request("GET", f"{url}/api/version", timeout=1.5).get("version")
            snap["running"] = True
        except OllamaError:
            snap["running"] = False
        if snap["running"]:
            snap["owner"] = "app" if self._owned() else "external"
            try:
                tags = _request("GET", f"{url}/api/tags", timeout=4).get("models") or []
                snap["models"] = sorted(
                    ({"name": m.get("name") or m.get("model", "?"), "size": int(m.get("size") or 0),
                      "modified": m.get("modified_at", ""), "details": m.get("details") or {}} for m in tags),
                    key=lambda m: m["name"],
                )
                snap["models_source"] = "server"
                ps = _request("GET", f"{url}/api/ps", timeout=4).get("models") or []
                snap["loaded"] = [
                    {"name": m.get("name") or m.get("model", "?"), "size": int(m.get("size") or 0),
                     "size_vram": int(m.get("size_vram") or 0), "expires_at": m.get("expires_at", "")}
                    for m in ps
                ]
            except OllamaError as exc:
                snap["error"] = f"Ollama answered but listing models failed: {exc}"
        elif snap["local"]:
            snap["models"] = scan_manifests()
            snap["models_source"] = "disk"
        if snap["version"] is None and binary:
            snap["version"] = binary_version(binary)
        snap["installed"] = bool(binary) or snap["running"]
        return snap

    def start_polling(self, interval_s: int = 5) -> None:
        self._pollers += 1
        if not self._poll_source:
            self._poll_source = GLib.timeout_add_seconds(max(1, int(interval_s)), self._poll_tick)
        self.refresh()

    def stop_polling(self) -> None:
        self._pollers = max(0, self._pollers - 1)
        if self._pollers == 0 and self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0

    def _poll_tick(self) -> bool:
        if not self._refreshing:
            self.refresh()
        return True

    def has_model(self, tag: str) -> bool:
        want = normalize_tag(tag)
        return any(normalize_tag(m["name"]) == want for m in self.status.get("models", []))

    # ---- ownership

    def _unit_active(self) -> bool:
        res = _run(["systemctl", "--user", "is-active", f"{UNIT}.service"], timeout=5)
        return bool(res) and res.stdout.strip() in ("active", "activating", "reloading")

    def _pid_alive(self) -> int | None:
        """The fallback process we started earlier (possibly before an app restart)."""
        if self._proc is not None and self._proc.poll() is None:
            return self._proc.pid
        try:
            pid = int(PID_FILE.read_text().strip())
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if any(b"ollama" in part for part in cmdline[:1]) and b"serve" in cmdline:
                return pid
        except (OSError, ValueError):
            pass
        return None

    def _owned(self) -> bool:
        return self._unit_active() or self._pid_alive() is not None

    # ---- start / stop / restart

    def start(self, on_done: Callable[[bool, str | None], None] | None = None) -> None:
        if self._busy:
            self._main(on_done, False, f"Ollama is busy ({self._busy})")
            return
        self._set_busy("starting", clear_error=True)
        self._spawn("start", self._action_worker, self._start_sync, on_done)

    def stop(self, on_done: Callable[[bool, str | None], None] | None = None) -> None:
        if self.status.get("running") and self.status.get("owner") != "app":
            msg = "Ollama was started outside the app, so the app leaves it running."
            self._set_busy(None, error=msg)
            self._main(on_done, False, msg)
            return
        if self._busy:
            self._main(on_done, False, f"Ollama is busy ({self._busy})")
            return
        self._set_busy("stopping", clear_error=True)
        self._spawn("stop", self._action_worker, self._stop_sync, on_done)

    def restart(self, on_done: Callable[[bool, str | None], None] | None = None) -> None:
        if self.status.get("running") and self.status.get("owner") != "app":
            msg = "Ollama was started outside the app, so the app can't restart it."
            self._set_busy(None, error=msg)
            self._main(on_done, False, msg)
            return
        if self._busy:
            self._main(on_done, False, f"Ollama is busy ({self._busy})")
            return
        self._set_busy("restarting", clear_error=True)

        def both() -> tuple[bool, str | None]:
            ok, err = self._stop_sync()
            return self._start_sync() if ok else (ok, err)

        self._spawn("restart", self._action_worker, both, on_done)

    def _action_worker(self, fn: Callable[[], tuple[bool, str | None]], on_done) -> None:
        try:
            ok, err = fn()
        except Exception as exc:
            log.exception("ollama action failed")
            ok, err = False, str(exc)
        self._main(self._action_finished, ok, err, on_done)

    def _action_finished(self, ok: bool, err: str | None, on_done) -> None:
        self._busy = None
        self._error = None if ok else err
        self.refresh(lambda _s: on_done(ok, err) if on_done else None)

    def _ping(self, url: str | None = None) -> bool:
        try:
            _request("GET", f"{url or self.url}/api/version", timeout=1.0)
            return True
        except OllamaError:
            return False

    def _server_env(self) -> dict[str, str]:
        host, port, _https = split_url(self.url)
        env = {
            "OLLAMA_HOST": f"127.0.0.1:{port}",
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
            "OLLAMA_KEEP_ALIVE": self._keep_alive(),
            "OLLAMA_CONTEXT_LENGTH": "4096",
            "OLLAMA_NO_CLOUD": "1",
        }
        if os.environ.get("OLLAMA_MODELS"):
            env["OLLAMA_MODELS"] = os.environ["OLLAMA_MODELS"]
        return env

    def _start_sync(self) -> tuple[bool, str | None]:
        url = self.url
        if not is_local_url(url):
            return False, "The Ollama address is on another computer, so the app can't start it."
        if self._ping(url):
            return True, None
        binary = find_binary()
        if binary is None:
            return False, "Ollama is not installed."
        env = self._server_env()
        launched = False
        if shutil.which("systemd-run") and not self._unit_active():
            _run(["systemctl", "--user", "reset-failed", f"{UNIT}.service"], timeout=5)
            cmd = ["systemd-run", "--user", f"--unit={UNIT}", "--collect", "--quiet",
                   "--description=Ollama (started by AI Linux)",
                   *[f"--setenv={k}={v}" for k, v in env.items()], binary, "serve"]
            res = _run(cmd, timeout=20)
            launched = bool(res) and res.returncode == 0
            if not launched:
                log.warning("systemd-run failed (%s); starting Ollama directly",
                            (res.stderr.strip() if res else "not run"))
        elif self._unit_active():
            launched = True  # still coming up from an earlier start
        if not launched:
            ok, err = self._popen(binary, env)
            if not ok:
                return False, err
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._ping(url):
                return True, None
            time.sleep(0.4)
            if not self._owned():
                return False, "Ollama stopped right after starting. " + self._last_log_line()
        return False, f"Ollama did not answer within {START_TIMEOUT_S} seconds."

    def _popen(self, binary: str, env: dict) -> tuple[bool, str | None]:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / "ollama.log", "ab") as logf:
                self._proc = subprocess.Popen(
                    [binary, "serve"], env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True,
                )
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(self._proc.pid))
            return True, None
        except OSError as exc:
            return False, f"Could not start Ollama: {exc}"

    def _last_log_line(self) -> str:
        res = _run(["journalctl", "--user", "-u", f"{UNIT}.service", "-n", "8", "--no-pager", "-o", "cat"],
                   timeout=5)
        lines = [ln for ln in (res.stdout.splitlines() if res else []) if ln.strip()]
        if not lines and (LOG_DIR / "ollama.log").exists():
            try:
                lines = (LOG_DIR / "ollama.log").read_text(errors="replace").splitlines()[-8:]
            except OSError:
                lines = []
        errs = [ln for ln in lines if "error" in ln.lower()]
        return (errs or lines or ["No log output."])[-1].strip()[:240]

    def _stop_sync(self) -> tuple[bool, str | None]:
        url = self.url
        if self._unit_active():
            res = _run(["systemctl", "--user", "stop", f"{UNIT}.service"], timeout=STOP_TIMEOUT_S + 10)
            if res is None or res.returncode != 0:
                return False, "Could not stop Ollama: " + (res.stderr.strip() if res else "systemctl failed")
        elif (pid := self._pid_alive()) is not None:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(int(STOP_TIMEOUT_S / 0.25)):
                    if self._pid_alive() is None:
                        break
                    time.sleep(0.25)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                return False, f"Could not stop Ollama: {exc}"
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                self._proc = None
            PID_FILE.unlink(missing_ok=True)
        elif self._ping(url):
            return False, "Ollama was started outside the app, so the app leaves it running."
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while time.monotonic() < deadline and self._ping(url):
            time.sleep(0.3)
        return (False, "Ollama is still answering after the stop.") if self._ping(url) else (True, None)

    # ---- pull / delete

    def pull(
        self,
        tag: str,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_done: Callable[[bool, str | None], None] | None = None,
    ) -> Handle:
        """Stream POST /api/pull; progress sums every layer seen so far. Returns a cancellable handle."""
        handle = Handle()
        self._spawn("pull", self._pull_worker, tag, handle, on_progress, on_done)
        return handle

    def _pull_worker(self, tag, handle: Handle, on_progress, on_done) -> None:
        host, port, https = split_url(self.url)
        conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
        conn = conn_cls(host, port, timeout=30)
        handle._conn = conn
        layers: dict[str, tuple[int, int]] = {}
        last = 0.0
        ok, err = False, None
        try:
            conn.request("POST", "/api/pull", json.dumps({"model": tag, "stream": True}),
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            if resp.status >= 400:
                raise OllamaError(_error_text(resp.read(), f"HTTP {resp.status}"))
            if conn.sock is not None:
                conn.sock.settimeout(None)  # digest checks can go quiet for a long time
            while not handle.cancelled:
                line = resp.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("error"):
                    raise OllamaError(msg["error"])
                status = str(msg.get("status", ""))
                if msg.get("digest") and msg.get("total"):
                    layers[msg["digest"]] = (int(msg.get("completed") or 0), int(msg["total"]))
                if status == "success":
                    ok = True
                now = time.monotonic()
                if on_progress and (now - last >= downloads.PROGRESS_EVERY_S or not msg.get("digest")):
                    last = now
                    done = sum(c for c, _t in layers.values())
                    total = sum(t for _c, t in layers.values())
                    self._main(on_progress, done, total, _pretty_status(status))
            if handle.cancelled:
                err = downloads.CANCELLED
            elif not ok:
                err = "The download stopped before it finished; try again to resume."
        except OllamaError as exc:
            err = str(exc)
        except (OSError, http.client.HTTPException) as exc:
            err = downloads.CANCELLED if handle.cancelled else f"Lost the connection to Ollama: {exc}"
        finally:
            handle._conn = None
            conn.close()
        self._main(self._pull_finished, ok, err, on_done)

    def _pull_finished(self, ok: bool, err: str | None, on_done) -> None:
        self.refresh()
        if on_done is not None:
            on_done(ok, err)

    def delete(self, tag: str, on_done: Callable[[bool, str | None], None] | None = None) -> None:
        def worker():
            try:
                _request("DELETE", f"{self.url}/api/delete", {"model": tag}, timeout=30)
                result = (True, None)
            except OllamaError as exc:
                result = (False, str(exc))
            self._main(self._pull_finished, *result, on_done)

        self._spawn("delete", worker)

    # ---- one-token decision (router)

    def decide(
        self,
        model: str,
        system: str,
        options: list[str],
        utterance: str,
        on_done: Callable[[dict | None, str | None], None],
        *,
        keep_alive: str | None = None,
        num_gpu: int | None = None,
        timeout: float = 60,
    ) -> None:
        """Ask ``model`` to pick one lettered option; on_done(result, error) on the main thread.

        result: index, letter, probs (normalised over the option letters), confidence, mass
        (raw probability that landed on any option letter) and latency_s.
        """
        if not options or len(options) > len(LETTERS):
            self._main(on_done, None, f"decide needs 1 to {len(LETTERS)} options")
            return
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": build_decision_prompt(system, options)},
                {"role": "user", "content": utterance},
            ],
            "stream": False,
            "think": False,
            "logprobs": True,
            "top_logprobs": len(options) + 2,
            "keep_alive": keep_alive or ROUTER_KEEP_ALIVE,
            "options": {"temperature": 0, "num_predict": 1},
        }
        if num_gpu is not None:
            body["options"]["num_gpu"] = num_gpu  # 0 keeps the small model on the CPU
        url = self.url

        def worker():
            t0 = time.monotonic()
            try:
                reply = _request("POST", f"{url}/api/chat", body, timeout=timeout)
                result = decision_from_reply(reply, len(options))
                result["latency_s"] = round(time.monotonic() - t0, 3)
                self._main(on_done, result, None)
            except OllamaError as exc:
                self._main(on_done, None, str(exc))
            except Exception as exc:
                log.exception("decide failed")
                self._main(on_done, None, str(exc))

        self._spawn("decide", worker)

    # ---- install the pinned release

    def install(
        self,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_done: Callable[[bool, str | None], None] | None = None,
    ) -> Handle:
        handle = Handle()
        if self._busy:
            self._main(on_done, False, f"Ollama is busy ({self._busy})")
            return handle
        self._set_busy("installing", clear_error=True)

        def progress(done, total, status):
            self._main(on_progress, done, total, status)

        def worker():
            try:
                ok, err = install_release(progress, handle.event)
            except Exception as exc:
                log.exception("ollama install crashed")
                ok, err = False, f"Install failed: {exc}"
            self._main(self._action_finished, ok, err, on_done)

        self._spawn("install", worker)
        return handle


def _pretty_status(status: str) -> str:
    if status.startswith("pulling") and status != "pulling manifest":
        return "Downloading"
    return {"pulling manifest": "Preparing", "verifying sha256 digest": "Verifying",
            "writing manifest": "Finishing", "success": "Done"}.get(status, status.capitalize())


def decision_from_reply(reply: dict, n_options: int) -> dict:
    """Turn an /api/chat reply with logprobs into the router's decision record."""
    logprobs = reply.get("logprobs")
    if logprobs is None:
        logprobs = (reply.get("message") or {}).get("logprobs")
    raw = letter_probs(logprobs, n_options)
    mass = sum(raw.values())
    if mass <= 0:
        text = (reply.get("message") or {}).get("content", "")
        raise OllamaError(f"The model did not answer with an option letter (got {text!r})")
    probs = {k: v / mass for k, v in raw.items()}
    letter = max(probs, key=probs.get)
    return {"index": LETTERS.index(letter), "letter": letter, "probs": probs,
            "confidence": probs[letter], "mass": mass}


class _CountingReader:
    def __init__(self, f, report):
        self.f, self.report, self.n = f, report, 0

    def read(self, size=-1):
        data = self.f.read(size)
        self.n += len(data)
        self.report(self.n)
        return data


def install_release(progress: Callable[[int, int, str], None], cancel: threading.Event,
                    asset: tuple[str, int, str] | None = None, url: str | None = None) -> tuple[bool, str | None]:
    """Download, verify and unpack the pinned release into MANAGED_DIR/<version>/ (blocking)."""
    asset = asset or install_asset()
    if asset is None:
        return False, f"No official Ollama build for this processor ({platform.machine()})."
    name, size, sha = asset
    url = url or RELEASE_URL.format(version=OLLAMA_PIN, asset=name)
    target = MANAGED_DIR / OLLAMA_PIN
    if (target / "bin" / "ollama").is_file():
        return True, None
    MANAGED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(MANAGED_DIR).free < size * 3.5:  # archive + unpacked libraries
        return False, f"Ollama needs about {size * 3.5 / 1e9:.1f} GB of free disk space."
    tarball = CACHE_DIR / f"ollama-{OLLAMA_PIN}-{name}"
    try:
        downloads.fetch(url, tarball, sha, lambda d, t: progress(d, t or size, "Downloading"), cancel, size)
    except downloads.Cancelled:
        return False, downloads.CANCELLED
    except downloads.DownloadError as exc:
        return False, str(exc)
    tmp = MANAGED_DIR / f".{OLLAMA_PIN}.unpacking"
    shutil.rmtree(tmp, ignore_errors=True)
    total = tarball.stat().st_size
    last = [0.0]

    def unpack_report(n):
        now = time.monotonic()
        if now - last[0] >= downloads.PROGRESS_EVERY_S:
            last[0] = now
            progress(n, total, "Unpacking")

    try:
        with open(tarball, "rb") as raw:
            mode = "r|zst" if name.endswith(".zst") else "r|gz"
            with tarfile.open(fileobj=_CountingReader(raw, unpack_report), mode=mode) as tf:
                for member in tf:
                    if cancel.is_set():
                        raise downloads.Cancelled()
                    tf.extract(member, tmp, filter="data")
        binary = tmp / "bin" / "ollama"
        if not binary.is_file():
            raise OllamaError("the archive has no bin/ollama")
        binary.chmod(0o755)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(tmp, target)
    except downloads.Cancelled:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, downloads.CANCELLED
    except (OSError, tarfile.TarError, OllamaError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, f"Could not unpack Ollama: {exc}"
    tarball.unlink(missing_ok=True)
    progress(total, total, "Done")
    return True, None
