"""Tests for the tray (ai_linux.tray) on a private bus, plus hotkey accelerator conversion. Run:
    PYTHONPATH=src python3 -m unittest tests.test_tray -v

The bus comes from ``dbus-run-session``, so the real session bus is never touched; a tiny in-process
fake org.kde.StatusNotifierWatcher stands in for the AppIndicator extension.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util  # noqa: E402
if importlib.util.find_spec("gi") is None:
    raise unittest.SkipTest("needs PyGObject (python3-gi)")
from pathlib import Path

from gi.repository import Gio, GLib

from ai_linux.config import APP_NAME, Settings
from ai_linux.hotkey import accel_label, accel_to_portal, portal_label
from ai_linux.state import Bus
from ai_linux.tray import (
    ICON_SIZES, ITEM_IFACE, ITEM_PATH, M_ALWAYS, M_HOTKEY, M_LISTEN, M_MODE, M_MUTE, M_OPEN, M_PREFS, M_QUIT,
    M_SEP, M_WAKE, MENU_IFACE, MENU_PATH, WATCHER_NAME, WATCHER_PATH, Tray,
)

_WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg name="service" type="s"/></signal>
  </interface>
</node>
"""

# Prints the private bus address, then holds the session open until stdin closes.
_HOLDER = "import os, sys; print(os.environ['DBUS_SESSION_BUS_ADDRESS'], flush=True); sys.stdin.read()"


def spin(pred=None, timeout: float = 3.0) -> bool:
    """Run the default main context until ``pred()`` is true or the timeout passes."""
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if pred is not None and pred():
            return True
        if time.monotonic() >= deadline:
            return pred() if pred is not None else True
        time.sleep(0.005)


def connect(address: str) -> Gio.DBusConnection:
    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    return Gio.DBusConnection.new_for_address_sync(address, flags, None, None)


def call(conn, dest, path, iface, method, params=None, reply=None) -> GLib.Variant:
    """Async call plus spin: the tray answers on this same main loop, so a sync call would deadlock."""
    box = {}
    conn.call(dest, path, iface, method, params, GLib.VariantType(reply) if reply else None,
              Gio.DBusCallFlags.NONE, 3000, None, lambda c, res: box.setdefault("res", res))
    if not spin(lambda: "res" in box):
        raise TimeoutError(method)
    return conn.call_finish(box["res"])


class FakeWatcher:
    """Owns org.kde.StatusNotifierWatcher and records every item that registers."""

    def __init__(self, address: str, refuse: bool = False):
        self.conn = connect(address)
        self.refuse = refuse
        self.items: list[tuple[str, str]] = []  # (service, sender)
        info = Gio.DBusNodeInfo.new_for_xml(_WATCHER_XML).interfaces[0]
        self._reg = self.conn.register_object(WATCHER_PATH, info, self._call, self._prop, None)
        self.owned = False
        self._own = Gio.bus_own_name_on_connection(
            self.conn, WATCHER_NAME, Gio.BusNameOwnerFlags.NONE,
            lambda *_: setattr(self, "owned", True), lambda *_: setattr(self, "owned", False))
        spin(lambda: self.owned)

    def _call(self, _conn, sender, _path, _iface, method, params, invocation):
        if method == "RegisterStatusNotifierItem":
            if self.refuse:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "no")
                return
            self.items.append((params.unpack()[0], sender))
        invocation.return_value(None)

    def _prop(self, _conn, _sender, _path, _iface, name):
        return {
            "RegisteredStatusNotifierItems": GLib.Variant("as", [s for s, _ in self.items]),
            "IsStatusNotifierHostRegistered": GLib.Variant("b", True),
            "ProtocolVersion": GLib.Variant("i", 0),
        }[name]

    def stop(self) -> None:
        if self._own:
            Gio.bus_unown_name(self._own)
            self._own = 0
        if self._reg:
            self.conn.unregister_object(self._reg)
            self._reg = 0
        self.conn.close_sync(None)


class FakeController:
    engine_available = True

    def __init__(self, settings):
        self.settings = settings
        self.calls: list[tuple] = []

    def toggle_listening(self):
        self.calls.append(("toggle",))

    def mute(self, on):
        self.calls.append(("mute", on))

    def is_muted(self):
        return bool(self.settings.get("muted")) or self.settings.get("listen_mode") == "off"

    def set_listen_mode(self, mode):
        self.calls.append(("mode", mode))


class FakeApp:
    def __init__(self):
        self.activations = 0
        self.actions: list[str] = []

    def activate(self):
        self.activations += 1

    def activate_action(self, name, _param):
        self.actions.append(name)

    def get_window(self):
        return None


class FakeCtx:
    def __init__(self, settings):
        self.settings = settings
        self.bus = Bus()
        self.controller = FakeController(settings)
        self.app = FakeApp()
        self.hotkey = None


@unittest.skipUnless(shutil.which("dbus-run-session"), "dbus-run-session is not installed")
class TrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._session = subprocess.Popen(
            ["dbus-run-session", "--", sys.executable, "-c", _HOLDER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        cls.address = cls._session.stdout.readline().strip()
        if not cls.address:
            cls._session.kill()
            raise unittest.SkipTest("dbus-run-session gave no bus address")
        # anything that asks for "the session bus" by mistake lands on the private one
        cls._old_env = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = cls.address
        cls.client = connect(cls.address)

    @classmethod
    def tearDownClass(cls):
        cls.client.close_sync(None)
        if cls._old_env is None:
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
        else:
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = cls._old_env
        cls._session.stdin.close()  # the holder exits and dbus-run-session stops its daemon
        try:
            cls._session.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls._session.kill()
            cls._session.wait(timeout=5)
        cls._session.stdout.close()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self._tmp.name) / "app.json")
        self.ctx = FakeCtx(self.settings)
        self.watcher = None
        self.tray = None
        self.tray_conn = connect(self.address)

    def tearDown(self):
        if self.tray is not None:
            self.tray.close()
        if self.watcher is not None:
            self.watcher.stop()
        spin(timeout=0.05)
        self.tray_conn.close_sync(None)
        self._tmp.cleanup()

    # helpers

    def make_tray(self, watcher: bool = True, refuse: bool = False) -> Tray:
        if watcher:
            self.watcher = FakeWatcher(self.address, refuse=refuse)
        self.tray = Tray(self.ctx, connection=self.tray_conn)
        if watcher and not refuse:
            self.assertTrue(spin(lambda: self.tray.available), "tray never registered")
        return self.tray

    def item_prop(self, name: str):
        out = call(self.client, self.tray.bus_name, ITEM_PATH, "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", (ITEM_IFACE, name)), "(v)")
        return out.unpack()[0]

    def menu(self, method: str, params: GLib.Variant, reply: str):
        return call(self.client, self.tray.bus_name, MENU_PATH, MENU_IFACE, method, params, reply).unpack()

    def layout(self, parent: int = 0, depth: int = -1, names=()) -> tuple:
        _rev, node = self.menu("GetLayout", GLib.Variant("(iias)", (parent, depth, list(names))), "(u(ia{sv}av))")
        return node

    def click(self, item_id: int, event: str = "clicked") -> None:
        self.menu("Event", GLib.Variant("(isvu)", (item_id, event, GLib.Variant("i", 0), 0)), "()")
        spin(timeout=0.1)  # clicks are dispatched from an idle callback

    # registration

    def test_registers_with_watcher(self):
        tray = self.make_tray()
        self.assertEqual(len(self.watcher.items), 1)
        service, sender = self.watcher.items[0]
        self.assertEqual(service, tray.bus_name)
        self.assertEqual(sender, self.tray_conn.get_unique_name())
        self.assertTrue(tray.bus_name.startswith("org.kde.StatusNotifierItem-"))

    def test_registers_when_watcher_appears_later(self):
        self.tray = Tray(self.ctx, connection=self.tray_conn)
        spin(timeout=0.3)
        self.assertFalse(self.tray.available)
        self.watcher = FakeWatcher(self.address)
        self.assertTrue(spin(lambda: self.tray.available))
        self.assertEqual([s for s, _ in self.watcher.items], [self.tray.bus_name])

    def test_unavailable_without_watcher(self):
        tray = self.make_tray(watcher=False)
        spin(timeout=0.4)
        self.assertFalse(tray.available)

    def test_watcher_refusal_keeps_it_unavailable(self):
        with self.assertLogs("ai_linux.tray", "WARNING") as logs:
            tray = self.make_tray(refuse=True)
            spin(timeout=0.4)
        self.assertFalse(tray.available)
        self.assertTrue(any("refused" in line for line in logs.output))

    def test_watcher_vanishing_marks_unavailable(self):
        tray = self.make_tray()
        self.watcher.stop()
        self.watcher = None
        self.assertTrue(spin(lambda: not tray.available))

    def test_close_releases_name_and_objects(self):
        tray = self.make_tray()
        name = tray.bus_name
        tray.close()
        self.assertFalse(tray.available)

        def gone():
            out = call(self.client, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                       "NameHasOwner", GLib.Variant("(s)", (name,)), "(b)")
            return not out.unpack()[0]

        self.assertTrue(spin(gone))
        tray.close()  # a second close is harmless

    # StatusNotifierItem properties

    def test_item_properties(self):
        self.make_tray()
        self.assertEqual(self.item_prop("Id"), "ai-linux")
        self.assertEqual(self.item_prop("Menu"), MENU_PATH)
        self.assertEqual(self.item_prop("Title"), APP_NAME)
        self.assertEqual(self.item_prop("Category"), "ApplicationStatus")
        self.assertEqual(self.item_prop("Status"), "Active")
        self.assertFalse(self.item_prop("ItemIsMenu"))
        self.assertEqual(self.item_prop("IconName"), "")  # hosts draw the live pixmap instead
        pixmaps = self.item_prop("IconPixmap")
        self.assertEqual([(w, h) for w, h, _ in pixmaps], [(s, s) for s in ICON_SIZES])
        for w, h, data in pixmaps:
            self.assertEqual(len(data), w * h * 4)
        _icon, _images, title, detail = self.item_prop("ToolTip")
        self.assertIn(APP_NAME, title)
        self.assertTrue(detail)

    def test_get_all_properties(self):
        self.make_tray()
        props = call(self.client, self.tray.bus_name, ITEM_PATH, "org.freedesktop.DBus.Properties", "GetAll",
                     GLib.Variant("(s)", (ITEM_IFACE,)), "(a{sv})").unpack()[0]
        for key in ("Id", "Menu", "Status", "IconPixmap", "ToolTip"):
            self.assertIn(key, props)
        self.assertEqual(props["Id"], "ai-linux")

    def test_custom_item_id(self):
        self.watcher = FakeWatcher(self.address)
        self.tray = Tray(self.ctx, item_id="ai-linux-test", connection=self.tray_conn)
        self.assertTrue(spin(lambda: self.tray.available))
        self.assertEqual(self.item_prop("Id"), "ai-linux-test")

    def test_menu_properties(self):
        self.make_tray()
        out = call(self.client, self.tray.bus_name, MENU_PATH, "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", (MENU_IFACE, "Version")), "(v)")
        self.assertEqual(out.unpack()[0], 3)

    # com.canonical.dbusmenu

    def test_layout_full_tree(self):
        self.make_tray()
        root_id, root_props, children = self.layout()
        self.assertEqual(root_id, 0)
        self.assertEqual(root_props.get("children-display"), "submenu")
        ids = [c[0] for c in children]
        self.assertEqual(ids, [M_OPEN, M_LISTEN, M_MUTE, M_MODE, M_SEP, M_PREFS, M_QUIT])
        by_id = {c[0]: c for c in children}
        self.assertEqual(by_id[M_OPEN][1]["label"], f"Open {APP_NAME}")
        self.assertEqual(by_id[M_LISTEN][1]["label"], "Listen now")
        self.assertEqual(by_id[M_SEP][1]["type"], "separator")
        self.assertEqual(by_id[M_MUTE][1]["toggle-type"], "checkmark")
        self.assertEqual(by_id[M_MUTE][1]["toggle-state"], 0)
        modes = by_id[M_MODE][2]
        self.assertEqual([m[0] for m in modes], [M_WAKE, M_HOTKEY, M_ALWAYS])
        self.assertEqual([m[1]["toggle-type"] for m in modes], ["radio"] * 3)
        self.assertEqual([m[1]["toggle-state"] for m in modes], [1, 0, 0])  # default listen_mode is wake

    def test_layout_depth_and_property_filter(self):
        self.make_tray()
        _id, _props, children = self.layout(depth=0)
        self.assertEqual(children, [])
        _id, _props, children = self.layout(depth=1, names=["label"])
        mode = next(c for c in children if c[0] == M_MODE)
        self.assertEqual(mode[2], [])  # one level only
        for child in children:
            self.assertLessEqual(set(child[1]), {"label"})
        _id, _props, kids = self.layout(parent=M_MODE)
        self.assertEqual([k[0] for k in kids], [M_WAKE, M_HOTKEY, M_ALWAYS])

    def test_layout_unknown_parent_is_an_error(self):
        self.make_tray()
        with self.assertRaises(GLib.Error) as cm:
            self.layout(parent=999)
        self.assertIn("InvalidArgs", Gio.DBusError.get_remote_error(cm.exception) or "")

    def test_group_properties_and_get_property(self):
        self.make_tray()
        (items,) = self.menu("GetGroupProperties", GLib.Variant("(aias)", ([M_QUIT, 999], ["label"])),
                             "(a(ia{sv}))")
        self.assertEqual(items, [(M_QUIT, {"label": "Quit"})])  # unknown ids are skipped
        (value,) = self.menu("GetProperty", GLib.Variant("(is)", (M_PREFS, "label")), "(v)")
        self.assertEqual(value, "Preferences")
        (need,) = self.menu("AboutToShow", GLib.Variant("(i)", (0,)), "(b)")
        self.assertFalse(need)

    def test_events_dispatch_to_controller_and_app(self):
        self.make_tray()
        c, app = self.ctx.controller, self.ctx.app
        self.click(M_LISTEN)
        self.assertEqual(c.calls[-1], ("toggle",))
        self.click(M_MUTE)
        self.assertEqual(c.calls[-1], ("mute", True))
        self.click(M_HOTKEY)
        self.assertEqual(c.calls[-1], ("mode", "hotkey"))
        self.click(M_ALWAYS)
        self.assertEqual(c.calls[-1], ("mode", "always"))
        self.click(M_OPEN)
        self.assertEqual(app.activations, 1)
        self.click(M_PREFS)
        self.click(M_QUIT)
        self.assertEqual(app.actions, ["preferences", "quit"])

    def test_mute_item_unmutes_when_muted(self):
        self.settings.set("listen_mode", "off")
        self.make_tray()
        self.click(M_MUTE)
        self.assertEqual(self.ctx.controller.calls[-1], ("mute", False))

    def test_non_click_events_are_ignored(self):
        self.make_tray()
        self.click(M_LISTEN, "hovered")
        self.click(M_QUIT, "opened")
        self.assertEqual(self.ctx.controller.calls, [])
        self.assertEqual(self.ctx.app.actions, [])

    def test_unknown_event_id_is_an_error(self):
        self.make_tray()
        with self.assertRaises(GLib.Error):
            self.click(999)

    def test_event_group(self):
        self.make_tray()
        events = [(M_LISTEN, "clicked", GLib.Variant("i", 0), 0), (999, "clicked", GLib.Variant("i", 0), 0)]
        (errors,) = self.menu("EventGroup", GLib.Variant("(a(isvu))", (events,)), "(ai)")
        spin(timeout=0.1)
        self.assertEqual(errors, [999])
        self.assertEqual(self.ctx.controller.calls, [("toggle",)])

    # StatusNotifierItem methods

    def test_activate_opens_and_secondary_toggles(self):
        self.make_tray()
        call(self.client, self.tray.bus_name, ITEM_PATH, ITEM_IFACE, "Activate", GLib.Variant("(ii)", (0, 0)))
        spin(timeout=0.1)
        self.assertEqual(self.ctx.app.activations, 1)
        call(self.client, self.tray.bus_name, ITEM_PATH, ITEM_IFACE, "SecondaryActivate",
             GLib.Variant("(ii)", (0, 0)))
        spin(timeout=0.1)
        self.assertEqual(self.ctx.controller.calls, [("toggle",)])

    def test_unknown_item_method_is_an_error(self):
        self.make_tray()
        with self.assertRaises(GLib.Error):
            call(self.client, self.tray.bus_name, ITEM_PATH, ITEM_IFACE, "NoSuchMethod")

    # live updates

    def test_state_change_updates_menu_and_signals(self):
        self.make_tray()
        seen: list[str] = []

        def on_signal(_c, _sender, _path, _iface, name, _params):
            seen.append(name)

        sub = self.client.signal_subscribe(self.tray.bus_name, None, None, None, None,
                                           Gio.DBusSignalFlags.NONE, on_signal)
        try:
            spin(timeout=0.1)
            self.ctx.bus.set_state("listening")
            self.assertTrue(spin(lambda: "ItemsPropertiesUpdated" in seen and "NewToolTip" in seen))
            (label,) = self.menu("GetProperty", GLib.Variant("(is)", (M_LISTEN, "label")), "(v)")
            self.assertEqual(label, "Stop listening")
            _icon, _images, title, _detail = self.item_prop("ToolTip")
            self.assertIn("Listening", title)
            self.assertTrue(spin(lambda: "NewIcon" in seen))  # the icon animates while listening
        finally:
            self.client.signal_unsubscribe(sub)

    def test_listen_mode_change_moves_radio_and_mute(self):
        self.make_tray()

        def state(item_id):
            return self.menu("GetProperty", GLib.Variant("(is)", (item_id, "toggle-state")), "(v)")[0]

        self.settings.set("listen_mode", "hotkey")
        spin(timeout=0.05)
        self.assertEqual([state(M_WAKE), state(M_HOTKEY), state(M_ALWAYS)], [0, 1, 0])
        self.settings.set("listen_mode", "off")
        spin(timeout=0.05)
        self.assertEqual(state(M_MUTE), 1)
        self.assertEqual(state(M_HOTKEY), 1)  # muting keeps the last real mode selected


class HotkeyAcceleratorTest(unittest.TestCase):
    def test_accel_to_portal(self):
        cases = {
            "<Super><Alt>space": "LOGO+ALT+space",
            "<Control><Shift>a": "CTRL+SHIFT+a",
            "<Primary>A": "CTRL+a",  # shift is a modifier, not a letter case
            "<ctrl><Control>x": "CTRL+x",  # duplicates collapse
            "<Mod4><Mod1>Tab": "LOGO+ALT+Tab",
            "<Super>F1": "LOGO+F1",
            "  <Super>  k ": "LOGO+k",
            "space": "space",
        }
        for accel, want in cases.items():
            with self.subTest(accel=accel):
                self.assertEqual(accel_to_portal(accel), want)

    def test_accel_to_portal_rejects_invalid(self):
        for accel in ("<Foo>a", "<Super>", "", None, "<Super>notakey", 42):
            with self.subTest(accel=accel):
                self.assertIsNone(accel_to_portal(accel))

    def test_portal_label(self):
        cases = {
            "LOGO+ALT+space": "Super+Alt+Space",
            "CTRL+a": "Ctrl+A",
            "CTRL+SHIFT+F1": "Ctrl+Shift+F1",
            "LOGO+Return": "Super+Return",
            "NUM+a": "NumLock+A",
            "": "",
            None: "",
        }
        for trigger, want in cases.items():
            with self.subTest(trigger=trigger):
                self.assertEqual(portal_label(trigger), want)

    def test_accel_label(self):
        self.assertEqual(accel_label("<Super><Alt>space"), "Super+Alt+Space")
        self.assertEqual(accel_label("garbage<"), "garbage<")  # unparseable shows as typed
        self.assertEqual(accel_label(""), "")
        self.assertEqual(accel_label(None), "")


if __name__ == "__main__":
    unittest.main()
