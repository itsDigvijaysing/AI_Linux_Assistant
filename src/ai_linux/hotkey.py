"""Global talk hotkey through the xdg-desktop-portal GlobalShortcuts interface (Wayland-safe).

Everything is async Gio D-Bus on a private connection: the host Registry must see our app id before
any other portal call on that connection, and GTK has already used the shared one.
"""

from __future__ import annotations

import itertools
import logging
import os
import re

from gi.repository import Gio, GLib, GObject

from ai_linux.config import APP_ID

try:  # keysym validation only; conversion still works without it
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk
except Exception:  # pragma: no cover
    Gdk = Gtk = None

log = logging.getLogger(__name__)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
SHORTCUT_ID = "toggle-listen"
SHORTCUT_DESCRIPTION = "Talk to Phoenix"

# GTK accelerator modifier names to the XDG shortcuts spec names.
_MODIFIERS = {
    "control": "CTRL", "ctrl": "CTRL", "ctl": "CTRL", "primary": "CTRL",
    "alt": "ALT", "mod1": "ALT",
    "shift": "SHIFT", "shft": "SHIFT",
    "super": "LOGO", "meta": "LOGO", "hyper": "LOGO", "mod4": "LOGO", "logo": "LOGO",
    "mod2": "NUM", "num": "NUM",
}
_PORTAL_LABELS = {"CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift", "LOGO": "Super", "NUM": "NumLock"}
_ACCEL_RE = re.compile(r"^((?:<[A-Za-z0-9_]+>)*)\s*([^<>\s]+)$")
_tokens = itertools.count(1)


def accel_to_portal(accel: str | None) -> str | None:
    """GTK accelerator to the portal trigger format, e.g. ``<Super><Alt>space`` to ``LOGO+ALT+space``.

    Keeps the modifier order as written; returns None for anything that is not a valid shortcut.
    """
    if not accel or not isinstance(accel, str):
        return None
    m = _ACCEL_RE.match(accel.strip())
    if not m:
        return None
    mods: list[str] = []
    for name in re.findall(r"<([A-Za-z0-9_]+)>", m.group(1)):
        mod = _MODIFIERS.get(name.lower())
        if mod is None:
            return None
        if mod not in mods:
            mods.append(mod)
    key = _keysym(m.group(2))
    if key is None:
        return None
    return "+".join([*mods, key])


def _keysym(key: str) -> str | None:
    """Canonical xkb keysym name, lowercase for letters (shift is a modifier, not a case)."""
    if Gdk is None:
        return key.lower() if len(key) == 1 else key
    for candidate in (key, key.lower(), key.capitalize()):
        kv = Gdk.keyval_from_name(candidate)
        if kv and kv != Gdk.KEY_VoidSymbol:
            return Gdk.keyval_name(Gdk.keyval_to_lower(kv))
    return None


def portal_label(trigger: str | None) -> str:
    """Readable form of a portal trigger, e.g. ``LOGO+ALT+space`` to ``Super+Alt+Space``."""
    if not trigger:
        return ""
    parts = trigger.split("+")
    key = parts[-1]
    if Gdk is not None:
        kv = Gdk.keyval_from_name(key)
        if kv and kv != Gdk.KEY_VoidSymbol:
            uni = Gdk.keyval_to_unicode(kv)
            key = chr(uni).upper() if uni and chr(uni).isprintable() and not chr(uni).isspace() else key
    key = key[:1].upper() + key[1:]
    return "+".join([*(_PORTAL_LABELS.get(p, p.capitalize()) for p in parts[:-1]), key])


def accel_label(accel: str | None) -> str:
    """Readable form of a GTK accelerator for tooltips and settings rows."""
    return portal_label(accel_to_portal(accel)) or (accel or "")


def readable_trigger(desc: str) -> str:
    """GNOME says 'Press <Alt><Super>space'; show 'Press Alt+Super+Space' (raw '<' also broke markup labels)."""
    return re.sub(r"(?:<\w+>)+[^\s<>]+", lambda m: accel_label(m.group(0)), desc).strip()


def _request_path(conn: Gio.DBusConnection, token: str) -> str:
    sender = conn.get_unique_name().lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{sender}/{token}"


class Hotkey(GObject.Object):
    """Binds ``toggle-listen`` once (GNOME asks the user to approve), then reacts to presses.

    ``available``: the portal session is up. ``bound``: the shortcut has a trigger.
    ``trigger_description``: what the desktop says the trigger is, or None.
    """

    available = GObject.Property(type=bool, default=False)
    bound = GObject.Property(type=bool, default=False)
    trigger_description = GObject.Property(type=str, default=None)

    def __init__(self, ctx, address: str | None = None, app_id: str = APP_ID, auto_bind: bool = True):
        super().__init__()
        self.ctx = ctx
        self.app_id = app_id
        self.auto_bind = auto_bind
        self.declined = False
        self._conn: Gio.DBusConnection | None = None
        self._session: str | None = None
        self._subs: list[int] = []
        self._requests: dict[str, int] = {}
        self._watch_id = 0
        self._pressed = False
        self._closed = False
        self._binding = False
        self._handlers = [ctx.settings.connect("changed::hotkey", self._on_hotkey_changed)]
        try:
            address = address or Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            Gio.DBusConnection.new_for_address(address, flags, None, None, self._on_connected)
        except Exception:
            log.exception("hotkey: no session bus")

    # ---- public ----

    def rebind(self) -> None:
        """Ask the desktop to bind the shortcut again (a new session, then BindShortcuts)."""
        if self._conn is None or self._closed:
            return
        self.declined = False
        self._close_session()
        self._create_session(then_bind=True)

    def configure(self, parent_window: str = "") -> None:
        """Open the desktop's own shortcut settings for us, where the portal supports it."""
        if self._conn is None or self._session is None:
            return
        self._conn.call(
            PORTAL, PORTAL_PATH, SHORTCUTS_IFACE, "ConfigureShortcuts",
            GLib.Variant("(osa{sv})", (self._session, parent_window, {})),
            None, Gio.DBusCallFlags.NONE, -1, None, self._log_result, "ConfigureShortcuts",
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pressed:
            self._release()
        for hid in self._handlers:
            try:
                self.ctx.settings.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        if self._conn is not None:
            self._close_session()
            for sid in [*self._subs, *self._requests.values()]:
                self._conn.signal_unsubscribe(sid)
            self._subs.clear()
            self._requests.clear()
            if self._watch_id:
                Gio.bus_unwatch_name(self._watch_id)
                self._watch_id = 0
            self._conn.close(None, None)  # async; the portal drops our session with the connection
        self.available = False

    # ---- connection and portal discovery ----

    def _on_connected(self, _src, res) -> None:
        try:
            conn = Gio.DBusConnection.new_for_address_finish(res)
        except GLib.Error as exc:
            log.warning("hotkey: cannot connect to the session bus: %s", exc.message)
            return
        if self._closed:
            conn.close(None, None)
            return
        self._conn = conn
        for member, cb in (("Activated", self._on_activated), ("Deactivated", self._on_deactivated),
                           ("ShortcutsChanged", self._on_shortcuts_changed)):
            self._subs.append(conn.signal_subscribe(
                PORTAL, SHORTCUTS_IFACE, member, PORTAL_PATH, None, Gio.DBusSignalFlags.NONE, cb))
        # AUTO_START so a portal that is installed but not yet running gets started.
        self._watch_id = Gio.bus_watch_name_on_connection(
            conn, PORTAL, Gio.BusNameWatcherFlags.AUTO_START, self._on_portal_appeared, self._on_portal_vanished)

    def _on_portal_appeared(self, conn, _name, _owner) -> None:
        if self._closed:
            return
        # Register must precede every other portal call on this connection; older portals lack it.
        conn.call(PORTAL, PORTAL_PATH, REGISTRY_IFACE, "Register", GLib.Variant("(sa{sv})", (self.app_id, {})),
                  None, Gio.DBusCallFlags.NONE, 5000, None, self._on_registered)

    def _on_portal_vanished(self, _conn, _name) -> None:
        if self._closed:
            return
        if self.available:
            log.info("hotkey: the desktop portal went away")
        else:
            log.info("hotkey: no xdg-desktop-portal on this session; the global hotkey is unavailable")
        self._session = None
        self.available = False
        self.bound = False

    def _on_registered(self, conn, res) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            if "App info not found" in exc.message:
                log.warning("hotkey: the portal cannot find %s.desktop; install it so the shortcut "
                            "dialog shows our name", self.app_id)
            else:
                log.info("hotkey: portal registry unavailable (%s)", exc.message)
        if self._closed:
            return
        conn.call(PORTAL, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                  GLib.Variant("(ss)", (SHORTCUTS_IFACE, "version")), GLib.VariantType("(v)"),
                  Gio.DBusCallFlags.NONE, 5000, None, self._on_version)

    def _on_version(self, conn, res) -> None:
        try:
            version = conn.call_finish(res).unpack()[0]
        except GLib.Error as exc:
            log.info("hotkey: the portal has no GlobalShortcuts (%s); the global hotkey is unavailable",
                     exc.message)
            self.available = False
            return
        if self._closed:
            return
        log.debug("hotkey: GlobalShortcuts version %s", version)
        self._create_session(then_bind=False)

    # ---- requests ----

    def _request(self, method: str, make_params, on_response) -> None:
        """Call a portal method that answers through a Request object's Response signal."""
        conn = self._conn
        token = f"ailinux{os.getpid()}_{next(_tokens)}"
        path = _request_path(conn, token)

        def response(_c, _sender, obj_path, _iface, _member, params):
            self._drop_request(obj_path)
            if self._closed:
                return
            code, results = params.unpack()
            try:
                on_response(code, results)
            except Exception:
                log.exception("hotkey: %s response failed", method)

        def sub(p: str) -> None:
            if p not in self._requests:
                self._requests[p] = conn.signal_subscribe(
                    PORTAL, REQUEST_IFACE, "Response", p, None, Gio.DBusSignalFlags.NO_MATCH_RULE
                    if False else Gio.DBusSignalFlags.NONE, response)

        sub(path)  # subscribe before calling so a fast Response is never missed

        def done(c, res):
            try:
                handle = c.call_finish(res).unpack()[0]
            except GLib.Error as exc:
                self._drop_request(path)
                log.warning("hotkey: %s failed: %s", method, exc.message)
                if not self._closed:
                    on_response(2, {})
                return
            if handle != path:  # pre-0.9 portals ignore handle_token
                self._drop_request(path)
                sub(handle)

        conn.call(PORTAL, PORTAL_PATH, SHORTCUTS_IFACE, method, make_params(token), None,
                  Gio.DBusCallFlags.NONE, -1, None, done)

    def _drop_request(self, path: str) -> None:
        sid = self._requests.pop(path, None)
        if sid is not None and self._conn is not None:
            self._conn.signal_unsubscribe(sid)

    def _create_session(self, then_bind: bool) -> None:
        def params(token):
            return GLib.Variant("(a{sv})", ({
                "handle_token": GLib.Variant("s", token),
                "session_handle_token": GLib.Variant("s", f"{token}_s"),
            },))

        def response(code, results):
            if code != 0 or not results.get("session_handle"):
                log.warning("hotkey: the portal refused a shortcuts session (code %s)", code)
                self.available = False
                return
            self._session = str(results["session_handle"])
            self.available = True
            if then_bind:
                self._bind()
            else:
                self._list()

        self._request("CreateSession", params, response)

    def _list(self) -> None:
        session = self._session

        def params(token):
            return GLib.Variant("(oa{sv})", (session, {"handle_token": GLib.Variant("s", token)}))

        def response(code, results):
            if session != self._session:
                return
            if code == 0 and self._apply(results.get("shortcuts", [])):
                return
            if self.auto_bind and not self.declined:
                self._bind()

        self._request("ListShortcuts", params, response)

    def _bind(self) -> None:
        """Opens the desktop's approval dialog the first time, so tests must fake the portal."""
        if self._binding or self._session is None:
            return
        session = self._session
        trigger = accel_to_portal(self.ctx.settings.get("hotkey"))
        opts = {"description": GLib.Variant("s", SHORTCUT_DESCRIPTION)}
        if trigger:
            opts["preferred_trigger"] = GLib.Variant("s", trigger)
        else:
            log.warning("hotkey: %r is not a valid shortcut; the desktop will ask for one",
                        self.ctx.settings.get("hotkey"))
        self._binding = True

        def params(token):
            return GLib.Variant("(oa(sa{sv})sa{sv})", (
                session, [(SHORTCUT_ID, opts)], "", {"handle_token": GLib.Variant("s", token)}))

        def response(code, results):
            self._binding = False
            if session != self._session:
                return
            if code == 1:
                self.declined = True  # don't re-ask this run; rebind() asks again on request
                log.info("hotkey: the user dismissed the shortcut dialog")
            if code != 0 or not self._apply(results.get("shortcuts", [])):
                self.bound = False

        self._request("BindShortcuts", params, response)

    def _apply(self, shortcuts) -> bool:
        """Record our shortcut's trigger from a portal list; True when it is bound."""
        for sid, info in shortcuts or []:
            if sid == SHORTCUT_ID:
                desc = readable_trigger(info.get("trigger_description") or "") or None
                self.trigger_description = desc
                self.bound = True
                log.info("hotkey: talk shortcut bound to %s", desc or "(no description)")
                return True
        return False

    def _close_session(self) -> None:
        session, self._session = self._session, None
        self.bound = False
        if session and self._conn is not None and not self._conn.is_closed():
            self._conn.call(PORTAL, session, SESSION_IFACE, "Close", None, None, Gio.DBusCallFlags.NONE,
                            5000, None, self._log_result, "Session.Close")

    @staticmethod
    def _log_result(conn, res, what) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            log.debug("hotkey: %s: %s", what, exc.message)

    # ---- signals ----

    def _ours(self, params) -> bool:
        session, sid = params.get_child_value(0).get_string(), params.get_child_value(1).get_string()
        return not self._closed and sid == SHORTCUT_ID and session == self._session

    def _on_activated(self, _c, _sender, _path, _iface, _member, params) -> None:
        if not self._ours(params):
            return
        try:
            if self.ctx.settings.get("hold_to_talk"):
                self._pressed = True
                self.ctx.controller.push_to_talk(True)
            else:
                self.ctx.controller.toggle_listening()
        except Exception:
            log.exception("hotkey: activation failed")

    def _on_deactivated(self, _c, _sender, _path, _iface, _member, params) -> None:
        if self._ours(params) and self._pressed:
            self._release()

    def _release(self) -> None:
        # Release whatever we pressed, even if hold_to_talk changed mid-press.
        self._pressed = False
        try:
            self.ctx.controller.push_to_talk(False)
        except Exception:
            log.exception("hotkey: release failed")

    def _on_shortcuts_changed(self, _c, _sender, _path, _iface, _member, params) -> None:
        session, shortcuts = params.unpack()
        if not self._closed and session == self._session:
            self._apply(shortcuts)

    def _on_hotkey_changed(self, *_args) -> None:
        if self._conn is None or self._closed or not self.available:
            return  # the startup flow reads the new value itself
        log.info("hotkey: setting changed, asking the desktop to rebind")
        self.rebind()
