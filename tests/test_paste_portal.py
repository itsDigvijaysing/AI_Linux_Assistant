"""The portal paste helper against a fake D-Bus connection: no real dialog, no real key presses."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import paste_portal as P  # noqa: E402

try:
    from gi.repository import GLib
except ImportError:  # the engine venv has no gi; the helper only runs on the system Python
    GLib = None

TOKENS = {"CreateSession": "createsession", "SelectDevices": "selectdevices", "Start": "start"}


class FakeConn:
    def __init__(self, responses):
        self.responses = responses  # method -> (response code, results)
        self.calls, self.subs, self.next = [], {}, 1

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, bus, iface, signal, path, arg0, flags, callback):
        self.subs[self.next] = (path, callback)
        self.next += 1
        return self.next - 1

    def signal_unsubscribe(self, sid):
        self.subs.pop(sid, None)

    def call_sync(self, bus, obj, iface, method, params, reply_type, flags, timeout, cancellable):
        self.calls.append((method, params.unpack()))
        if method in self.responses:
            code, results = self.responses[method]
            for path, callback in list(self.subs.values()):
                if path.endswith("/" + TOKENS[method]):
                    reply = GLib.Variant("(ua{sv})", (code, results))
                    GLib.idle_add(lambda cb=callback, p=path, r=reply: cb(self, P.BUS, p, P.REQUEST, "Response", r)
                                  and False)


@unittest.skipIf(GLib is None, "python3-gi not available")
class PortalChainTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.token = tmp / "state" / "token"

    def conn(self, start=0):
        return FakeConn({"CreateSession": (0, {"session_handle": GLib.Variant("s", "/s/1")}),
                         "SelectDevices": (0, {}),
                         "Start": (start, {"restore_token": GLib.Variant("s", "new-token")})})

    def test_pastes_and_keeps_the_new_token(self):
        conn = self.conn()
        self.assertEqual(P.PortalPaste(conn, "ctrl-v", self.token).run(), P.OK)
        keys = [(args[2], args[3]) for method, args in conn.calls if method == "NotifyKeyboardKeysym"]
        k = P.KEYSYMS
        self.assertEqual(keys, [(k["ctrl"], 1), (k["v"], 1), (k["v"], 0), (k["ctrl"], 0)])
        self.assertEqual(self.token.read_text(), "new-token")
        self.assertEqual(self.token.stat().st_mode & 0o777, 0o600)

    def test_saved_token_is_offered_and_keyboard_only(self):
        self.token.parent.mkdir()
        self.token.write_text("old-token")
        conn = self.conn()
        P.PortalPaste(conn, "ctrl-v", self.token).run()
        opts = next(args[1] for method, args in conn.calls if method == "SelectDevices")
        self.assertEqual((opts["restore_token"], opts["types"], opts["persist_mode"]), ("old-token", 1, 2))

    def test_authorize_sends_no_keys(self):
        conn = self.conn()
        self.assertEqual(P.PortalPaste(conn, None, self.token).run(), P.OK)
        self.assertFalse(any(method == "NotifyKeyboardKeysym" for method, _ in conn.calls))
        self.assertEqual(self.token.read_text(), "new-token")

    def test_refused_dialog_sends_nothing(self):
        conn = self.conn(start=1)
        self.assertEqual(P.PortalPaste(conn, "ctrl-v", self.token).run(), P.CANCELLED)
        self.assertFalse(any(method == "NotifyKeyboardKeysym" for method, _ in conn.calls))
        self.assertFalse(self.token.exists())


class TerminalTests(unittest.TestCase):
    def test_terminal_names(self):
        for name in ("gnome-terminal-server", "Ptyxis", "org.gnome.Console", "kitty", "st", "tui.btop"):
            self.assertTrue(P.is_terminal(name), name)
        for name in ("com.system76.CosmicEdit", "firefox", "Code", "", None):
            self.assertFalse(P.is_terminal(name), name)

    def test_key_sequences(self):
        self.assertEqual([k for k, down, _ in P.keys_for("ctrl-shift-v") if down], ["ctrl", "shift", "v"])
        self.assertEqual([k for k, down, _ in P.keys_for("ctrl-shift-v") if not down], ["v", "shift", "ctrl"])
        self.assertEqual([k for k, _d, _p in P.keys_for("shift-insert")], ["shift", "insert", "insert", "shift"])
        self.assertEqual(P.resolve_mode("shift-insert"), "shift-insert")


if __name__ == "__main__":
    unittest.main()
