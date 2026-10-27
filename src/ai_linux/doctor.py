"""System readiness checks for the Health page and the diagnostic report (no GTK needed)."""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from ai_linux import __version__, models_catalog
from ai_linux.paths import APP_NAME, DATA_DIR, MODELS_DIR
from ai_linux.schema import DEFAULTS

log = logging.getLogger(__name__)

STATUSES = ("ok", "warn", "fail", "info")
SEVERITY = {"info": 0, "ok": 0, "warn": 1, "fail": 2}
GROUPS = (
    ("voice", "Voice"),
    ("models", "Models"),
    ("desktop", "Desktop"),
    ("system", "About this system"),
)
FIXES = ("enable_autostart", "start_ollama", "open_models")
DESKTOP_TOOLS = {"brightnessctl": "brightnessctl", "playerctl": "playerctl", "wl-copy": "wl-clipboard",
                 "ydotool": "ydotool"}
AEC_GLOBS = ("/usr/lib/*/spa-0.2/aec/libspa-aec-webrtc.so", "/usr/lib/spa-0.2/aec/libspa-aec-webrtc.so",
             "/usr/lib64/spa-0.2/aec/libspa-aec-webrtc.so")
PCI_IDS = ("/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids")
GPU_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
SYS_ROOT = Path("/sys")


@dataclass
class Check:
    id: str
    title: str
    status: str  # ok | warn | fail | info
    detail: str = ""
    fix_id: str | None = None  # one of FIXES, offered by the Health page
    group: str = "system"  # one of GROUPS


@dataclass(frozen=True)
class _Spec:
    id: str
    title: str
    group: str
    fn: object


CHECKS: list[_Spec] = []


def _check(check_id: str, title: str, group: str):
    def deco(fn):
        CHECKS.append(_Spec(check_id, title, group, fn))
        return fn
    return deco


class _Probe:
    """Per-run memo so checks that share a slow probe (pw-cli, Ollama) run it once."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._locks: dict[str, threading.Lock] = {}
        self._values: dict[str, object] = {}
        self._guard = threading.Lock()

    def once(self, key: str, fn):
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._values:
                self._values[key] = fn()
            return self._values[key]

    def setting(self, key: str):
        try:
            return self.ctx.settings.get(key)
        except Exception:
            return DEFAULTS.get(key)

    def explicit(self, key: str) -> bool:
        """True only when the user changed ``key``: a default is not a choice to report on."""
        try:
            return bool(self.ctx.settings.is_set(key))
        except Exception:
            return False


def _run(cmd: list[str], timeout: float = 2.0) -> tuple[int, str] | None:
    """Run a short command; None when it isn't installed. Timeouts raise, the check reports them."""
    if shutil.which(cmd[0]) is None:
        return None
    env = dict(os.environ, LC_ALL="C")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout


def _http_json(url: str, timeout: float = 1.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read(1_000_000).decode("utf-8", "replace"))


def _which(name: str) -> str | None:
    # The session PATH may lack ~/.local/bin when launched from the desktop.
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local/bin" / name
    return str(local) if os.access(local, os.X_OK) else None


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# ---- PipeWire ----

def _pw_nodes(probe: _Probe) -> list[dict] | None:
    def fetch():
        res = _run(["pw-cli", "ls", "Node"], timeout=2.0)
        if res is None or res[0] != 0:
            return None
        nodes, cur = [], None
        for line in res[1].splitlines():
            s = line.strip()
            if s.startswith("id ") and "type" in s:
                cur = {}
                nodes.append(cur)
            elif cur is not None and " = " in s:
                k, _, v = s.partition(" = ")
                cur[k.strip()] = v.strip().strip('"')
        return nodes
    return probe.once("pw_nodes", fetch)


def _pw_version(probe: _Probe) -> tuple[bool, str]:
    def fetch():
        res = _run(["pw-cli", "info", "0"], timeout=2.0)
        if res is None:
            return None
        if res[0] != 0:
            return False, ""
        for line in res[1].splitlines():
            s = line.strip()
            if s.startswith("version:"):
                return True, s.split(":", 1)[1].strip().strip('"')
        return True, ""
    return probe.once("pw_version", fetch)


@_check("engine", "Speech engine", "voice")
def check_engine(probe: _Probe) -> Check:
    controller = getattr(probe.ctx, "controller", None)
    engine = getattr(controller, "engine", None) if controller is not None else None
    if engine is not None:
        proc = getattr(engine, "proc", None)
        if proc is not None and proc.poll() is None:
            settings = getattr(probe.ctx, "settings", None)
            profile = settings.active_profile() if hasattr(settings, "active_profile") else ""
            return Check("engine", "Speech engine", "ok",
                         f"Running{f' with the {profile} settings' if profile else ''}", group="voice")
        return Check("engine", "Speech engine", "warn", "Set up, but not running", group="voice")
    if controller is not None and getattr(controller, "demo", None) is not None:
        return Check("engine", "Speech engine", "info", "Demo mode: replies are simulated", group="voice")
    from ai_linux.engine_client import find_python
    if find_python() is None:
        return Check("engine", "Speech engine", "fail", "Its Python environment is missing: run the installer",
                     group="voice")
    return Check("engine", "Speech engine", "info", "Ready to start", group="voice")


@_check("pipewire", "PipeWire audio", "voice")
def check_pipewire(probe: _Probe) -> Check:
    res = _pw_version(probe)
    if res is None:
        return Check("pipewire", "PipeWire audio", "fail",
                     "PipeWire tools not found. Voice needs PipeWire (package pipewire-bin).", group="voice")
    running, version = res
    if not running:
        return Check("pipewire", "PipeWire audio", "fail",
                     "PipeWire isn't running, so the assistant can't hear or speak.", group="voice")
    return Check("pipewire", "PipeWire audio", "ok",
                 f"Running, version {version}" if version else "Running", group="voice")


@_check("microphone", "Microphone", "voice")
def check_microphone(probe: _Probe) -> Check:
    nodes = _pw_nodes(probe)
    if nodes is None:
        return Check("microphone", "Microphone", "warn", "Can't list microphones while PipeWire is down.",
                     group="voice")
    mics = [n for n in nodes if n.get("media.class", "").startswith("Audio/Source")]
    if not mics:
        return Check("microphone", "Microphone", "fail", "No microphone found. Plug one in or check Sound settings.",
                     group="voice")
    names = [m.get("node.description") or m.get("node.nick") or m.get("node.name", "Microphone") for m in mics]
    detail = names[0] if len(names) == 1 else f"{names[0]} and {len(names) - 1} more"
    return Check("microphone", "Microphone", "ok", detail, group="voice")


@_check("aec", "Echo cancellation", "voice")
def check_aec(probe: _Probe) -> Check:
    if any(glob.glob(p) for p in AEC_GLOBS):
        return Check("aec", "Echo cancellation", "ok", "Available, so you can talk over replies on speakers",
                     group="voice")
    return Check("aec", "Echo cancellation", "warn",
                 "WebRTC echo cancellation not found: interrupting on speakers won't work. "
                 "Install libspa-0.2-modules.", group="voice")


# ---- Ollama and models ----

_OLLAMA_WORDS = {
    "running": ("running", "ready", "up", "ok", "serving", "online", "started"),
    "starting": ("starting", "loading", "launching", "booting"),
    "stopped": ("stopped", "not_running", "down", "offline", "off", "idle", "inactive", "exited"),
    "missing": ("missing", "not_installed", "absent", "unavailable", "none"),
    "error": ("error", "failed", "crashed"),
}


def _normalize_ollama_status(status) -> str | None:
    """Map whatever OllamaManager.status holds (str, Enum, dict, object) onto our five words."""
    if status is None:
        return None
    if isinstance(status, Enum):
        status = status.value if isinstance(status.value, str) else status.name
    if isinstance(status, dict):
        status = status.get("state") or status.get("status") or (
            "running" if status.get("running") else "missing" if status.get("installed") is False else None)
    elif not isinstance(status, str):
        if getattr(status, "running", None):
            return "running"
        if getattr(status, "installed", None) is False:
            return "missing"
        status = getattr(status, "state", None) or getattr(status, "name", None)
    if not isinstance(status, str):
        return None
    word = status.strip().lower().replace("-", "_").replace(" ", "_")
    for norm, words in _OLLAMA_WORDS.items():
        if word in words:
            return norm
    return None


def _ollama_binary(mgr) -> str | None:
    for attr in ("binary", "bin_path", "binary_path", "executable"):
        val = getattr(mgr, attr, None)
        if isinstance(val, (str, Path)) and os.access(val, os.X_OK):
            return str(val)
    found = _which("ollama")
    if found:
        return found
    for cand in (DATA_DIR / "ollama/bin/ollama", Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")):
        if os.access(cand, os.X_OK):
            return str(cand)
    return None


def _ollama(probe: _Probe) -> dict:
    def fetch():
        mgr = getattr(probe.ctx, "ollama", None)
        url = str(probe.setting("ollama_url") or DEFAULTS["ollama_url"]).rstrip("/")
        info = {"url": url, "running": False, "version": "", "tags": None, "state": None,
                "can_start": callable(getattr(mgr, "start", None))}
        try:
            info["version"] = str(_http_json(url + "/api/version").get("version", ""))
            info["running"] = True
        except Exception as exc:
            log.debug("ollama probe %s: %s", url, exc)
        if info["running"]:
            try:
                models = _http_json(url + "/api/tags", timeout=2.0).get("models", [])
                info["tags"] = {m.get("name", "") for m in models} | {m.get("model", "") for m in models}
            except Exception as exc:
                log.debug("ollama tags: %s", exc)
        else:
            try:
                st = getattr(mgr, "status", None)
                info["state"] = _normalize_ollama_status(st() if callable(st) else st)
            except Exception as exc:
                log.debug("ollama manager status: %s", exc)
        info["binary"] = _ollama_binary(mgr)
        return info
    return probe.once("ollama", fetch)


@_check("ollama", "Ollama", "models")
def check_ollama(probe: _Probe) -> Check:
    o = _ollama(probe)
    if o["running"]:
        return Check("ollama", "Ollama", "ok", f"Running, version {o['version']}" if o["version"] else "Running",
                     group="models")
    fix = "start_ollama" if o["can_start"] else None
    if o["state"] == "starting":
        return Check("ollama", "Ollama", "info", "Starting up", group="models")
    if o["binary"] is None and o["state"] in (None, "missing"):
        return Check("ollama", "Ollama", "fail", "Not installed. It runs the language models on this computer.",
                     group="models")
    if o["state"] == "error":
        return Check("ollama", "Ollama", "fail", "Failed to start. See the logs folder.", fix_id=fix,
                     group="models")
    return Check("ollama", "Ollama", "warn", "Installed but not running, so the assistant can't think.",
                 fix_id=fix, group="models")


def _model_on_disk(spec: models_catalog.ModelSpec) -> bool:
    helper = getattr(models_catalog, "local_path", None)
    cands = []
    if callable(helper):
        try:
            cands.append(Path(helper(spec, MODELS_DIR)))
        except Exception as exc:
            log.debug("models_catalog.local_path: %s", exc)
    repo = spec.source.replace("/", "--")
    cands += [MODELS_DIR / spec.id, MODELS_DIR / spec.source.split("/")[-1], MODELS_DIR / repo,
              MODELS_DIR / f"models--{repo}"]
    for c in cands:
        try:
            if c.is_file() or (c.is_dir() and any(c.iterdir())):
                return True
        except OSError:
            continue
    return False


def _ollama_manifest(name: str) -> bool:
    """Whether Ollama's store holds ``name``, read from disk so it works while the server is stopped."""
    base, _, tag = name.partition(":")
    path = base if "/" in base else f"library/{base}"
    roots = [Path(os.environ["OLLAMA_MODELS"])] if os.environ.get("OLLAMA_MODELS") else []
    roots += [Path.home() / ".ollama/models", Path("/usr/share/ollama/.ollama/models")]
    return any(_is_file(r / "manifests/registry.ollama.ai" / path / (tag or "latest")) for r in roots)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:  # Python < 3.14 raises on an unreadable dir (the system Ollama's store) instead of False
        return False


def _tag_present(tags: set[str], *names: str) -> bool:
    norm = {t[:-7] if t.endswith(":latest") else t for t in tags if t}
    return any((n[:-7] if n.endswith(":latest") else n) in norm for n in names)


def _check_slot(probe: _Probe, slot: str) -> Check:
    check_id = f"model_{slot}"
    title = {"small": "Small model", "big": "Big model", "speech": "Speech recognition"}[slot]
    chosen = str(probe.setting(models_catalog.SLOT_KEYS[slot]) or "")
    if slot == "small" and chosen == "none":
        return Check(check_id, title, "ok", "None: the big model picks every command", group="models")
    spec = models_catalog.get(chosen)
    if spec is None:
        spec = next((m for m in models_catalog.for_slot(slot) if m.recommended), None)
    if spec is None:
        return Check(check_id, title, "warn", "No model chosen", fix_id="open_models", group="models")
    size = f"{spec.download_gb:.1f} GB"
    if spec.runtime == "ollama":
        tags = _ollama(probe)["tags"]
        if tags is not None:
            present = _tag_present(tags, spec.source, spec.id)
        else:
            present = _ollama_manifest(spec.source) or _ollama_manifest(spec.id)
        present = present or _model_on_disk(spec)
    elif spec.runtime == "onnx":
        from ai_linux import downloads
        present = downloads.speech_installed(spec.id, MODELS_DIR / "ASR")
    elif spec.runtime == "openvino":
        from ai_linux import downloads
        present = downloads.openvino_installed(spec.id, models_catalog.local_path(spec, MODELS_DIR))  # full size
    else:
        present = _model_on_disk(spec)
    if present:
        return Check(check_id, title, "ok", f"{spec.name} is installed", group="models")
    return Check(check_id, title, "warn", f"{spec.name} isn't downloaded yet ({size})", fix_id="open_models",
                 group="models")


@_check("model_speech", "Speech recognition", "voice")
def check_model_speech(probe: _Probe) -> Check:
    c = _check_slot(probe, "speech")
    return Check(c.id, c.title, c.status, c.detail, fix_id=c.fix_id, group="voice")


@_check("model_small", "Small model", "models")
def check_model_small(probe: _Probe) -> Check:
    return _check_slot(probe, "small")


@_check("model_big", "Big model", "models")
def check_model_big(probe: _Probe) -> Check:
    return _check_slot(probe, "big")


# ---- GPUs: sysfs only. nvidia-smi and /proc/driver/nvidia wake a suspended dGPU. ----

@lru_cache(maxsize=32)
def _pci_name(vendor: str, device: str) -> str:
    v, d = vendor.lower().removeprefix("0x"), device.lower().removeprefix("0x")
    for path in PCI_IDS:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                in_vendor = False
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    if not line.startswith("\t"):
                        if in_vendor:
                            break
                        in_vendor = line[:4].lower() == v
                    elif in_vendor and not line.startswith("\t\t") and line[1:5].lower() == d:
                        return line[5:].strip()
        except OSError:
            continue
    return ""


def list_gpus(sys_root: Path = SYS_ROOT) -> list[dict]:
    gpus = []
    for dev in sorted((sys_root / "bus/pci/devices").glob("*")):
        try:
            cls = (dev / "class").read_text().strip()
            if not cls.startswith("0x03"):
                continue
            vendor = (dev / "vendor").read_text().strip().lower()
            device = (dev / "device").read_text().strip().lower()
        except OSError:
            continue
        driver = os.path.basename(os.readlink(dev / "driver")) if (dev / "driver").is_symlink() else ""
        try:
            runtime = (dev / "power/runtime_status").read_text().strip()
        except OSError:
            runtime = ""
        gpus.append({"slot": dev.name, "vendor": vendor, "device": device, "driver": driver, "runtime": runtime,
                     "brand": GPU_VENDORS.get(vendor, "Other")})
    return gpus


_RUNTIME_WORDS = {"active": "awake", "suspended": "asleep to save power", "suspending": "going to sleep",
                  "resuming": "waking up"}


@_check("gpu", "Graphics", "models")
def check_gpus(probe: _Probe, sys_root: Path = SYS_ROOT) -> list[Check]:
    gpus = list_gpus(sys_root)
    if not gpus:
        return [Check("gpu", "Graphics", "info", "No graphics card found: models run on the CPU", group="models")]
    out = []
    for g in gpus:
        name = _pci_name(g["vendor"], g["device"])
        parts = [name] if name else []
        parts.append(f"driver {g['driver']}" if g["driver"] else "no driver loaded")
        if g["runtime"] in _RUNTIME_WORDS:
            parts.append(_RUNTIME_WORDS[g["runtime"]])
        detail = ", ".join(parts)
        title = f"{g['brand']} graphics"
        brand = g["brand"]
        if brand == "NVIDIA":
            status = "ok" if g["driver"] == "nvidia" else "warn"
            if status == "warn":
                detail += ". Install the NVIDIA driver so the big model can use this GPU."
        elif brand == "AMD":
            status = "ok" if g["driver"] == "amdgpu" else "warn"
        else:
            status = "info"
        out.append(Check(f"gpu:{g['slot']}", title, status, detail, group="models"))
    return out


# ---- Desktop integration ----

@_check("tools", "Desktop tools", "desktop")
def check_tools(probe: _Probe) -> Check:
    missing = [b for b in DESKTOP_TOOLS if _which(b) is None]
    if not missing:
        return Check("tools", "Desktop tools", "ok", "Brightness, media, clipboard and typing tools are installed",
                     group="desktop")
    pkgs = " ".join(sorted({DESKTOP_TOOLS[b] for b in missing}))
    return Check("tools", "Desktop tools", "warn",
                 f"Missing {_join(missing)}, so some actions won't work. Install with: sudo apt install {pkgs}",
                 group="desktop")


def _dbus_call(name: str, path: str, iface: str, method: str, args=None, reply: str | None = None):
    from gi.repository import Gio, GLib  # GLib only, no GTK

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    params = GLib.Variant(*args) if args else None
    rtype = GLib.VariantType(reply) if reply else None
    return bus.call_sync(name, path, iface, method, params, rtype, Gio.DBusCallFlags.NONE, 1000, None).unpack()


def _portal_has(iface: str) -> bool:
    from gi.repository import Gio

    xml = _dbus_call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                     "org.freedesktop.DBus.Introspectable", "Introspect", reply="(s)")[0]
    return Gio.DBusNodeInfo.new_for_xml(xml).lookup_interface(iface) is not None


@_check("shortcuts", "Global shortcut", "desktop")
def check_shortcuts(probe: _Probe) -> Check:
    try:
        ok = _portal_has("org.freedesktop.portal.GlobalShortcuts")
    except Exception as exc:
        log.debug("portal introspect: %s", exc)
        ok = False
    if ok:
        return Check("shortcuts", "Global shortcut", "ok", "Available: the hotkey works in every app",
                     group="desktop")
    return Check("shortcuts", "Global shortcut", "warn",
                 "The desktop doesn't offer global shortcuts. Use the wake word or the tray instead.", group="desktop")


@_check("tray", "Tray icon", "desktop")
def check_tray(probe: _Probe) -> Check:
    try:
        owned = _dbus_call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                           ("(s)", ("org.kde.StatusNotifierWatcher",)), "(b)")[0]
    except Exception as exc:
        log.debug("tray watcher: %s", exc)
        owned = False
    if owned:
        return Check("tray", "Tray icon", "ok", "The top bar can show the assistant's icon", group="desktop")
    return Check("tray", "Tray icon", "warn",
                 "No tray in the top bar. Enable the AppIndicator extension to see the icon.", group="desktop")


@_check("pill", "Floating indicator", "desktop")
def check_pill(probe: _Probe) -> Check:
    pill = getattr(probe.ctx, "pill", None)
    if pill is not None:  # the running indicator knows which backend it got; probing would only guess
        if getattr(pill, "available", False):
            how = "the layer-shell protocol" if getattr(pill, "backend", None) == "layer-shell" else "Xwayland"
            return Check("pill", "Floating indicator", "ok", f"Floats above other windows through {how}",
                         group="desktop")
        return Check("pill", "Floating indicator", "warn",
                     "Can't float above other windows on this desktop, so it stays hidden. See the app log.",
                     group="desktop")
    if os.environ.get("DISPLAY"):
        return Check("pill", "Floating indicator", "ok", "Xwayland is available, so the indicator can float on top",
                     group="desktop")
    hint = " (Xwayland is installed but not running)" if shutil.which("Xwayland") else ""
    return Check("pill", "Floating indicator", "warn",
                 f"Needs Xwayland to float above other windows{hint}. It stays hidden for now.", group="desktop")


@_check("autostart", "Start at login", "desktop")
def check_autostart(probe: _Probe) -> Check:
    from ai_linux import autostart

    if autostart.is_enabled():
        return Check("autostart", "Start at login", "ok", "On: ready in the background after you sign in",
                     group="desktop")
    if probe.setting("autostart") and probe.explicit("autostart"):
        return Check("autostart", "Start at login", "warn", "Turned on, but the login entry is missing",
                     fix_id="enable_autostart", group="desktop")
    return Check("autostart", "Start at login", "info", "Off: start the app yourself after signing in",
                 fix_id="enable_autostart", group="desktop")


# ---- Versions ----

def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _session() -> str:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":")[-1]
    kind = os.environ.get("XDG_SESSION_TYPE", "")
    kind = {"wayland": "Wayland", "x11": "X11"}.get(kind, kind)
    return " on ".join(p for p in (desktop, kind) if p)


@_check("python", "Python", "system")
def check_python(probe: _Probe) -> Check:
    v = sys.version_info
    return Check("python", "Python", "info", f"{v.major}.{v.minor}.{v.micro}", group="system")


@_check("toolkit", "GTK and libadwaita", "system")
def check_toolkit(probe: _Probe) -> Check:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    gtk = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    adw = f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"
    return Check("toolkit", "GTK and libadwaita", "info", f"GTK {gtk}, libadwaita {adw}", group="system")


@_check("os", "Operating system", "system")
def check_os(probe: _Probe) -> Check:
    parts = [_os_name(), f"kernel {platform.release()}", _session()]
    return Check("os", "Operating system", "info", ", ".join(p for p in parts if p), group="system")


# ---- Runner and report ----

def _run_one(spec: _Spec, probe: _Probe, results: list, i: int) -> None:
    try:
        res = spec.fn(probe)
        res = res if isinstance(res, list) else [res]
        for c in res:
            if c.status not in STATUSES:
                c.status = "warn"
        results[i] = res
    except Exception as exc:
        log.warning("health check %s failed: %s", spec.id, exc)
        detail = "Timed out" if isinstance(exc, subprocess.TimeoutExpired) else f"Couldn't check: {exc}"
        results[i] = [Check(spec.id, spec.title, "warn", detail, group=spec.group)]


def run_checks(ctx_like=None, timeout: float = 5.0, only: list[str] | None = None) -> list[Check]:
    """Run every check in parallel; a slow or broken one is reported, never fatal. Call off the main thread."""
    probe = _Probe(ctx_like)
    specs = [s for s in CHECKS if only is None or s.id in only]
    results: list = [None] * len(specs)
    # Daemon threads: a stuck probe must never hold up quitting the app.
    threads = [threading.Thread(target=_run_one, args=(s, probe, results, i), daemon=True, name=f"doctor-{s.id}")
               for i, s in enumerate(specs)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    out: list[Check] = []
    for spec, res in zip(specs, list(results)):
        out.extend(res if res is not None else [Check(spec.id, spec.title, "warn", "Timed out", group=spec.group)])
    return out


def summarize(checks: list[Check]) -> tuple[str, int]:
    """Worst status and how many checks need attention (warn or fail)."""
    worst = max((c.status for c in checks), key=lambda s: SEVERITY.get(s, 0), default="ok")
    worst = "ok" if worst == "info" else worst
    return worst, sum(1 for c in checks if c.status in ("warn", "fail"))


def _scrub(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if home and home != "/" else text


def report_text(checks: list[Check]) -> str:
    """Plain-text report to paste when asking for help: no settings, secrets or home path."""
    lines = [
        f"{APP_NAME} diagnostic report",
        f"Version: {__version__}",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
    ]
    names = dict(GROUPS)
    order = [g for g, _ in GROUPS] + sorted({c.group for c in checks} - set(names))
    for group in order:
        rows = [c for c in checks if c.group == group]
        if not rows:
            continue
        lines.append(names.get(group, group.title()))
        for c in rows:
            lines.append(f"  [{c.status.upper():4}] {c.title}: {c.detail}".rstrip(": "))
        lines.append("")
    worst, attention = summarize(checks)
    lines.append(f"Summary: {attention} item(s) need attention" if attention else "Summary: everything is ready")
    return _scrub("\n".join(lines)) + "\n"
