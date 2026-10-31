#!/usr/bin/env python3
"""Paste the clipboard at the cursor through the RemoteDesktop portal: no uinput, no ydotool daemon, no sudo.

Ported from OpenWhispr's resources/linux-fast-paste.c (portal paste and AT-SPI terminal check; MIT, see
LICENSES/OpenWhispr.txt). Needs the system Python with python3-gi, so the engine runs it as a short helper, one paste
per run. The portal asks once; its restore token is kept in --token-file so later pastes run silently.

    paste_portal.py paste [--mode auto|ctrl-v|ctrl-shift-v|shift-insert] [--token-file PATH]
    paste_portal.py authorize --token-file PATH    ask now, send no keys
    paste_portal.py detect                         prints terminal, app or unknown
    paste_portal.py probe                          exit 0 when the portal offers a keyboard
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

BUS, OBJ = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
IFACE, REQUEST = "org.freedesktop.portal.RemoteDesktop", "org.freedesktop.portal.Request"
CALL_TIMEOUT_MS = 3000  # a stale session can hang a call, so every call is bounded (OpenWhispr #1614)
WATCHDOG_S = 10
DIALOG_S = 90  # the first paste shows the permission dialog: give the person time to answer
KEYBOARD, PERSISTENT = 1, 2
KEYSYMS = {"ctrl": 0xFFE3, "shift": 0xFFE1, "v": 0x0076, "insert": 0xFF63}
OK, FAILED, DENIED, CANCELLED, TIMED_OUT, NO_KEYBOARD, KEYS_FAILED = 0, 1, 2, 3, 4, 5, 6
MODES = ("ctrl-v", "ctrl-shift-v", "shift-insert")

# Window or app names of terminals, which paste with Ctrl+Shift+V (OpenWhispr's terminal_classes).
TERMINALS = (
    "konsole", "gnome-terminal", "terminal", "kitty", "alacritty", "terminator", "xterm", "urxvt", "rxvt", "tilix",
    "terminology", "wezterm", "foot", "st", "yakuake", "ghostty", "guake", "tilda", "hyper", "tabby", "sakura",
    "warp", "termius", "waveterm", "ptyxis", "kgx", "org.gnome.console", "cosmicterm", "xst", "stterm",
)
TERMINAL_PREFIXES = ("org.omarchy.", "tui.")


def _has_word(name: str, word: str) -> bool:
    """Short names like "st" must be a whole word, or "CosmicEdit" would count as a terminal."""
    name, word = name.lower(), word.lower()
    if len(word) > 2:
        return word in name
    start = name.find(word)
    while start != -1:
        end = start + len(word)
        if (start == 0 or not name[start - 1].isalnum()) and (end == len(name) or not name[end].isalnum()):
            return True
        start = name.find(word, start + 1)
    return False


def is_terminal(name: str | None) -> bool:
    if not name:
        return False
    if name.lower().startswith(TERMINAL_PREFIXES):
        return True
    return any(_has_word(name, t) for t in TERMINALS)


def detect_terminal() -> bool | None:
    """Is the app with the active window a terminal? None when AT-SPI can't tell."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.set_timeout(500, -1)
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win is not None and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                    return is_terminal(app.get_name())
    except Exception:
        return None
    return None


def keys_for(mode: str) -> list[tuple[str, bool, float]]:
    """(key, pressed, pause after) for one paste; the pauses let the compositor see modifiers first."""
    if mode == "shift-insert":
        return [("shift", True, 0.02), ("insert", True, 0.02), ("insert", False, 0), ("shift", False, 0)]
    mods = ["ctrl", "shift"] if mode == "ctrl-shift-v" else ["ctrl"]
    return ([(m, True, 0) for m in mods[:-1]] + [(mods[-1], True, 0.02), ("v", True, 0.02), ("v", False, 0)]
            + [(m, False, 0) for m in reversed(mods)])


def resolve_mode(mode: str) -> str:
    return mode if mode in MODES else ("ctrl-shift-v" if detect_terminal() else "ctrl-v")


def read_token(path: Path | None) -> str | None:
    try:
        return path.read_text().strip() or None if path else None
    except OSError:
        return None


def save_token(path: Path | None, token: str) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.replace(tmp, path)


class PortalPaste:
    """CreateSession, SelectDevices (keyboard, persistent), Start, then the paste keys: the C helper's chain."""

    def __init__(self, conn, mode: str | None, token_file: Path | None):
        from gi.repository import GLib
        self.GLib = GLib
        self.conn, self.mode, self.token_file = conn, mode, token_file
        self.loop = GLib.MainLoop()
        self.code = OK
        self.session = ""
        self._sub = 0
        sender = conn.get_unique_name()[1:].replace(".", "_")
        self._request = f"{OBJ}/request/{sender}/"

    def _call(self, method: str, signature: str, args: tuple):
        from gi.repository import Gio
        return self.conn.call_sync(BUS, OBJ, IFACE, method, self.GLib.Variant(signature, args), None,
                                   Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)

    def _request_then(self, token: str, method: str, signature: str, args: tuple, on_response) -> None:
        """Subscribe to the request's Response before calling, so a fast reply can't be missed."""
        from gi.repository import Gio

        def got(_conn, _sender, _path, _iface, _signal, params):
            self.conn.signal_unsubscribe(self._sub)
            response, results = params.unpack()
            on_response(response, results)

        self._sub = self.conn.signal_subscribe(BUS, REQUEST, "Response", self._request + token, None,
                                               Gio.DBusSignalFlags.NO_MATCH_RULE, got)
        try:
            self._call(method, signature, args)
        except Exception as exc:
            self.conn.signal_unsubscribe(self._sub)
            self._finish(FAILED, f"{method} failed: {exc}")

    def _finish(self, code: int, why: str = "") -> None:
        self.code = code
        if why:
            print(why, file=sys.stderr)
        self.loop.quit()

    def run(self) -> int:
        def watchdog():
            self._finish(TIMED_OUT, "Timeout waiting for portal response")
            return False

        self.GLib.timeout_add_seconds(WATCHDOG_S if read_token(self.token_file) else DIALOG_S, watchdog)
        opts = {"handle_token": self.GLib.Variant("s", "createsession"),
                "session_handle_token": self.GLib.Variant("s", "ailinux")}
        self._request_then("createsession", "CreateSession", "(a{sv})", (opts,), self._created)
        if self.code == OK:
            self.loop.run()
        return self.code

    def _created(self, response: int, results: dict) -> None:
        if response != 0:
            return self._finish(DENIED, f"CreateSession denied (response={response})")
        self.session = results["session_handle"]
        V = self.GLib.Variant
        opts = {"handle_token": V("s", "selectdevices"), "types": V("u", KEYBOARD), "persist_mode": V("u", PERSISTENT)}
        if token := read_token(self.token_file):
            opts["restore_token"] = V("s", token)
        self._request_then("selectdevices", "SelectDevices", "(oa{sv})", (self.session, opts), self._selected)

    def _selected(self, response: int, _results: dict) -> None:
        if response != 0:
            return self._finish(DENIED, f"SelectDevices denied (response={response})")
        opts = {"handle_token": self.GLib.Variant("s", "start")}
        self._request_then("start", "Start", "(osa{sv})", (self.session, "", opts), self._started)

    def _started(self, response: int, results: dict) -> None:
        if response != 0:
            return self._finish(CANCELLED, f"Start cancelled (response={response})")
        if token := results.get("restore_token"):
            save_token(self.token_file, token)
        if self.mode is None:
            return self._finish(OK)
        try:
            for key, pressed, pause in keys_for(self.mode):
                self._call("NotifyKeyboardKeysym", "(oa{sv}iu)", (self.session, {}, KEYSYMS[key], int(pressed)))
                if pause:
                    time.sleep(pause)
        except Exception as exc:
            return self._finish(KEYS_FAILED, f"sending keys failed: {exc}")
        self._finish(OK)


def has_keyboard(conn) -> bool:
    from gi.repository import Gio, GLib
    try:
        reply = conn.call_sync(BUS, OBJ, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", (IFACE, "AvailableDeviceTypes")), None,
                               Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)
    except Exception:
        return False
    return bool(reply.unpack()[0] & KEYBOARD)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("paste", "authorize", "detect", "probe"))
    parser.add_argument("--mode", default="auto", choices=("auto", *MODES))
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args(argv)
    if args.action == "detect":
        found = detect_terminal()
        print("unknown" if found is None else "terminal" if found else "app")
        return OK
    from gi.repository import Gio
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception as exc:
        print(f"D-Bus connection failed: {exc}", file=sys.stderr)
        return FAILED
    if not has_keyboard(conn):
        print("portal keyboard input unavailable", file=sys.stderr)
        return NO_KEYBOARD
    if args.action == "probe":
        return OK
    mode = resolve_mode(args.mode) if args.action == "paste" else None
    return PortalPaste(conn, mode, args.token_file).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
