"""The Todoist token's way from the Tools page to the keyring and the engine (fake keyring, fake Todoist)."""

from __future__ import annotations

import io
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gi.repository import GLib  # noqa: E402

from ai_linux.engine import todoist  # noqa: E402
from ai_linux.todoist_link import TodoistLink  # noqa: E402


class FakeSettings(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


class FakeKeyring:
    def __init__(self, token=None):
        self.items = {"todoist": token} if token else {}
        self.calls = []

    def lookup(self, name):
        self.calls.append(("lookup", name))
        return self.items.get(name)

    def store(self, name, value, label):
        self.calls.append(("store", name))
        self.items[name] = value

    def clear(self, name):
        self.calls.append(("clear", name))
        return 1 if self.items.pop(name, None) else 0


def api(check):
    return SimpleNamespace(check=check, TokenRejected=todoist.TokenRejected, TodoistError=todoist.TodoistError)


class LinkTests(unittest.TestCase):
    def make(self, connected=False, token=None, check=lambda t: 2):
        self.settings = FakeSettings(todoist_connected=connected)
        self.keyring = FakeKeyring(token)
        self.sent = []
        self.result = None
        return TodoistLink(self.settings, lambda n, v: self.sent.append((n, v)), store=self.keyring, api=api(check))

    def done(self, ok, text):
        self.result = (ok, text)

    def wait(self):
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + 3
        while self.result is None and time.monotonic() < deadline:
            ctx.iteration(False)
            time.sleep(0.005)
        for _ in range(5):  # the settings and engine updates queued alongside
            ctx.iteration(False)
        self.assertIsNotNone(self.result, "no answer")
        return self.result

    def test_a_good_token_is_kept_and_sent(self):
        link = self.make()
        link.save("  dummy-token  ", self.done)
        self.assertEqual(self.wait(), (True, "Connected. 2 tasks due today or overdue."))
        self.assertEqual(self.keyring.items, {"todoist": "dummy-token"})
        self.assertTrue(self.settings["todoist_connected"])
        self.assertEqual(self.sent, [("todoist", "dummy-token")])
        self.assertNotIn("dummy-token", json.dumps(self.settings))  # only the flag is a setting

    def test_a_rejected_token_is_not_kept(self):
        def check(token):
            raise todoist.TokenRejected("HTTP 401")
        link = self.make(check=check)
        link.save("wrong", self.done)
        ok, text = self.wait()
        self.assertFalse(ok)
        self.assertIn("didn't accept", text)
        self.assertEqual((self.keyring.items, self.sent, self.settings["todoist_connected"]), ({}, [], False))

    def test_offline_keeps_nothing(self):
        def check(token):
            raise todoist.TodoistError("no network")
        link = self.make(check=check)
        link.save("dummy-token", self.done)
        self.assertIn("Couldn't reach Todoist", self.wait()[1])
        self.assertEqual(self.keyring.items, {})

    def test_load_never_opens_the_keyring_unless_connected(self):
        link = self.make(connected=False, token="dummy-token")
        link.load(self.done)
        self.assertEqual(self.keyring.calls, [])

    def test_load_sends_the_saved_token(self):
        link = self.make(connected=True, token="dummy-token")
        link.load(self.done)
        self.assertEqual(self.wait(), (True, "Connected"))
        self.assertEqual(self.sent, [("todoist", "dummy-token")])

    def test_load_with_the_token_gone_from_the_keyring(self):
        link = self.make(connected=True)
        link.load(self.done)
        self.assertFalse(self.wait()[0])
        self.assertFalse(self.settings["todoist_connected"])

    def test_a_keyring_that_fails_at_start_is_reported(self):
        link = self.make(connected=True, token="dummy-token")
        changed = []
        link.on_change = lambda: changed.append(link.busy)

        def locked(name):
            raise RuntimeError("the keyring stayed locked")
        self.keyring.lookup = locked
        link.load(self.done)
        self.assertFalse(self.wait()[0])
        self.assertIn("the keyring stayed locked", link.problem)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.settings["todoist_connected"])  # the token is still there: the next start tries again
        self.assertEqual(changed, [False])  # the Tools page redraws, with its controls back on

    def test_a_good_load_or_save_clears_the_problem(self):
        link = self.make(connected=True, token="dummy-token")
        link.problem = "old"
        link.load(self.done)
        self.wait()
        self.assertEqual(link.problem, "")
        link.problem, self.result = "old", None
        link.save("dummy-token", self.done)
        self.wait()
        self.assertEqual(link.problem, "")

    def test_disconnect_clears_the_problem(self):
        link = self.make(connected=True, token="dummy-token")
        link.problem = "old"
        link.disconnect(self.done)
        self.wait()
        self.assertEqual(link.problem, "")

    def test_disconnect_forgets_everywhere(self):
        link = self.make(connected=True, token="dummy-token")
        link.disconnect(self.done)
        self.assertEqual(self.sent, [("todoist", "")])  # the engine forgets at once
        self.assertTrue(self.wait()[0])
        self.assertEqual((self.keyring.items, self.settings["todoist_connected"]), ({}, False))

    def test_empty_paste(self):
        link = self.make()
        link.save("   ", self.done)
        self.assertEqual(self.result, (False, "Paste your API token first."))


class EngineRestartTests(unittest.TestCase):
    def test_tokens_reach_every_new_engine_after_its_settings(self):
        from ai_linux import controller as controller_mod
        from ai_linux import engine_client
        from ai_linux.state import Bus

        lines = []

        class Proc:
            def __init__(self, *a, **k):
                self.stdin = SimpleNamespace(write=lambda s: lines.append(json.loads(s)), flush=lambda: None)
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO("")
                self.pid = 0

            def poll(self):
                return None

        settings = mock.Mock(get=mock.Mock(return_value=None), connect=mock.Mock(return_value=1))
        c = controller_mod.Controller(settings, Bus())
        c.set_secret("todoist", "dummy-token")  # loaded from the keyring before the engine started
        with mock.patch.object(engine_client, "find_python", return_value="/bin/true"), \
                mock.patch.object(engine_client, "LOG_DIR", Path(self.tmp())), \
                mock.patch.object(engine_client.subprocess, "Popen", Proc), \
                mock.patch.object(engine_client.threading, "Thread"):
            c._ensure_engine()
            c.engine.start()
        self.assertEqual([x["cmd"] for x in lines], ["settings", "secrets"])
        self.assertEqual(lines[1]["values"], {"todoist": "dummy-token"})
        self.assertNotIn("dummy-token", json.dumps(lines[0]))
        c.set_secret("todoist", "")
        self.assertEqual(lines[-1], {"cmd": "secrets", "values": {"todoist": ""}})
        self.assertEqual(c.secrets, {})

    def tmp(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name


if __name__ == "__main__":
    unittest.main()
