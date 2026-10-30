"""Installed desktop apps, read from .desktop files (no GTK in the engine process)."""

from __future__ import annotations

import configparser
import difflib
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class App:
    id: str  # desktop id without .desktop
    name: str
    keywords: tuple[str, ...] = ()
    comment: str = ""  # what it is for, from the .desktop Comment or GenericName
    exe: str = ""  # the program's file name, from Exec; "" when a launcher (env, python3, sh) hides it
    categories: tuple[str, ...] = ()
    pwa: bool = False  # a browser's web app: closing its program would close the browser


def _dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home, *data_dirs.split(":"), "/var/lib/flatpak/exports/share",
            str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/snapd/desktop"]
    return [Path(d) / "applications" for d in dict.fromkeys(dirs) if d]


# Exec words that start another program: the process to close isn't named after them.
_LAUNCHERS = {"sh", "bash", "dash", "zsh", "python", "python3", "perl", "ruby", "node", "java", "mono", "wine",
              "steam", "snap", "gio", "xdg-open", "exo-open", "pkexec", "sudo", "nice", "ionice", "taskset",
              "systemd-run", "gtk-launch", "bwrap", "firejail", "electron", "appimage-run", "gamemoderun",
              "prime-run", "switcherooctl", "uwsm", "uwsm-app"}


def exe_name(argv: list[str]) -> str:
    """The file name of the program an Exec line runs; "flatpak" for Flatpaks, "" when a launcher hides it."""
    for tok in argv:
        base = os.path.basename(tok)
        if base == "env" or ("=" in tok and not tok.startswith(("/", "-"))):
            continue  # env VAR=value program
        if base == "flatpak":
            return "flatpak"
        return "" if base in _LAUNCHERS or tok.startswith("%") else base
    return ""


_CACHE: dict = {"at": 0.0, "apps": []}
_CACHE_LOCK = threading.Lock()


def installed_cached(max_age: float = 30.0) -> list[App]:
    """installed(), scanned at most every max_age seconds: one request looks the list up several times."""
    with _CACHE_LOCK:
        if time.monotonic() - _CACHE["at"] > max_age or not _CACHE["apps"]:
            _CACHE["apps"], _CACHE["at"] = installed(), time.monotonic()
        return _CACHE["apps"]


def installed() -> list[App]:
    """Visible applications, first entry wins for a duplicate id (user dir before system)."""
    seen: dict[str, App] = {}
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "GNOME").split(":")
    for d in _dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.desktop")):
            app_id = str(f.relative_to(d))[:-8].replace("/", "-")
            if app_id in seen:
                continue
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("Type") != "Application" or e.get("NoDisplay") == "true" or e.get("Hidden") == "true":
                seen[app_id] = None  # type: ignore[assignment]  # a hidden user entry masks the system one
                continue
            only = [x for x in e.get("OnlyShowIn", "").split(";") if x]
            if only and not set(only) & set(desktop):
                continue
            kw = tuple(k.strip().lower() for k in e.get("Keywords", "").split(";") if k.strip())
            generic = e.get("GenericName", "").strip().lower()
            exe = (e.get("Exec") or "").split()
            cats = tuple(c for c in e.get("Categories", "").split(";") if c)
            seen[app_id] = App(app_id, e.get("Name", app_id).strip(), kw + ((generic,) if generic else ()),
                               (e.get("Comment") or e.get("GenericName") or "").strip(),
                               exe_name(exe), cats,
                               any(x.startswith(("--app-id", "--app=")) for x in exe))
    return sorted((a for a in seen.values() if a), key=lambda a: a.name.lower())


_ALIASES = {"vs code": "code", "vscode": "code", "files": "nautilus", "file manager": "nautilus",
            "terminal": "ptyxis", "chrome": "google chrome", "brave": "brave browser",
            "editor": "text editor", "settings": "settings"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def match(name: str, apps: list[App]) -> tuple[App | None, float]:
    """Best app for a spoken name, with a 0..1 score."""
    q = _norm(_ALIASES.get(_norm(name), name))
    if not q:
        return None, 0.0
    best, score = None, 0.0
    for a in apps:
        n, i = _norm(a.name), _norm(a.id.split(".")[-1])
        s = max(difflib.SequenceMatcher(None, q, n).ratio(), difflib.SequenceMatcher(None, q, i).ratio())
        if q in (n, i) or q == i.replace(" ", ""):
            s = 1.0
        elif n.startswith(q + " ") or q in n.split():
            s = max(s, 0.9)
        elif q in a.keywords:
            s = max(s, 0.85)
        if s > score:
            best, score = a, s
    return best, score


def candidates(name: str, apps: list[App], n: int = 8) -> list[App]:
    """The closest apps to a spoken name, for a decision model to choose from."""
    q = _norm(name)
    scored = sorted(apps, key=lambda a: -max(difflib.SequenceMatcher(None, q, _norm(a.name)).ratio(),
                                               0.9 if q and q in a.keywords else 0.0))
    return scored[:n]


GROUPS = (("Network", "for the internet"), ("Development", "for coding"), ("Office", "for office work"),
          ("AudioVideo", "for music and video"), ("Graphics", "for pictures"), ("Game", "games"),
          ("Settings", "for settings"), ("System", "system tools"), ("Utility", "utilities"))


def summary(apps: list[App]) -> str:
    """'63 apps: 12 for the internet, 8 for coding, ...' (each app counted once, in its first group)."""
    counts: dict[str, int] = {}
    for a in apps:
        g = next((label for cat, label in GROUPS if cat in a.categories), None)
        if g:
            counts[g] = counts.get(g, 0) + 1
    parts = [f"{n} {label}" for label, n in sorted(counts.items(), key=lambda kv: -kv[1])[:5]]
    return f"You have {len(apps)} apps" + (": " + ", ".join(parts) if parts else "") + "."


def on_bus(app_id: str) -> bool:
    """Is a running app holding its D-Bus name (a GApplication that can be raised or asked to quit)?"""
    try:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                            "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner", app_id],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "true" in r.stdout


def running(exe: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), exe[:15]], capture_output=True,
                              timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
