"""Installed desktop apps, read from .desktop files (no GTK in the engine process)."""

from __future__ import annotations

import configparser
import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class App:
    id: str  # desktop id without .desktop
    name: str
    keywords: tuple[str, ...] = ()


def _dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home, *data_dirs.split(":"), "/var/lib/flatpak/exports/share",
            str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/snapd/desktop"]
    return [Path(d) / "applications" for d in dict.fromkeys(dirs) if d]


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
            seen[app_id] = App(app_id, e.get("Name", app_id).strip(), kw + ((generic,) if generic else ()))
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
