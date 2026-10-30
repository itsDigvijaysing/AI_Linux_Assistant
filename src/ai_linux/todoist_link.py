"""Todoist's API token: kept in the user's keyring, checked against Todoist, handed to the engine in memory.

The ``todoist_connected`` setting only says a token was saved, so the keyring is never touched for anyone who
didn't connect Todoist. Keyring and network calls run on worker threads; results come back on the main loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from gi.repository import GLib

from ai_linux import secret_store
from ai_linux.engine import todoist

log = logging.getLogger(__name__)

NAME = "todoist"
LABEL = "Phoenix: Todoist API token"
Done = Callable[[bool, str], None]


class TodoistLink:
    def __init__(self, settings, send_secret: Callable[[str, str], None], store=secret_store, api=todoist):
        self.settings, self.send_secret = settings, send_secret
        self.store, self.api = store, api
        self.busy = False

    @property
    def connected(self) -> bool:
        return bool(self.settings.get("todoist_connected"))

    def _work(self, fn, done: Done | None) -> None:
        def run():
            try:
                ok, text = fn()
            except Exception as exc:  # the keyring or the network failed in a way we didn't expect
                log.exception("todoist")
                ok, text = False, f"Something went wrong: {exc}"
            GLib.idle_add(finish, ok, text)

        def finish(ok, text):
            self.busy = False
            if done is not None:
                done(ok, text)
            return False
        self.busy = True
        threading.Thread(target=run, name="Todoist", daemon=True).start()

    def load(self, done: Done | None = None) -> None:
        """At start: give the engine the saved token (may show the keyring's unlock dialog)."""
        if not self.connected:
            return

        def fn():
            token = self.store.lookup(NAME)
            if not token:
                GLib.idle_add(lambda: self.settings.set("todoist_connected", False) and False)
                return False, "The token is no longer in your keyring. Add it again."
            GLib.idle_add(lambda: self.send_secret(NAME, token) and False)
            return True, "Connected"
        self._work(fn, done)

    def save(self, token: str, done: Done | None = None) -> None:
        """Checks the token with Todoist, then keeps it in the keyring and hands it to the engine."""
        token = token.strip()
        if not token:
            if done is not None:
                done(False, "Paste your API token first.")
            return

        def fn():
            try:
                due = self.api.check(token)
            except self.api.TokenRejected:
                return False, "Todoist didn't accept that token. Copy it again from Todoist."
            except self.api.TodoistError:
                return False, "Couldn't reach Todoist to check the token. Try again when you're online."
            self.store.store(NAME, token, LABEL)

            def apply():
                self.settings.set("todoist_connected", True)
                self.send_secret(NAME, token)
                return False
            GLib.idle_add(apply)
            return True, f"Connected. {due} task{'s' * (due != 1)} due today or overdue."
        self._work(fn, done)

    def disconnect(self, done: Done | None = None) -> None:
        """Forgets the token: the engine first, then the keyring."""
        self.send_secret(NAME, "")
        self.settings.set("todoist_connected", False)

        def fn():
            self.store.clear(NAME)
            return True, "Disconnected. The token was removed from your keyring."
        self._work(fn, done)
