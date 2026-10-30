"""Secrets in the user's login keyring (GNOME Keyring, KeePassXC, ...) through the freedesktop Secret Service.

Only Gio is used, which every python3-gi install has; libsecret's typelib (gir1.2-secret-1) is not on a default
Ubuntu desktop. Every call blocks on D-Bus and may wait for an unlock prompt, so call it from a worker thread.
Spec: https://specifications.freedesktop.org/secret-service-spec/latest/
"""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.secrets"
SERVICE_PATH = "/org/freedesktop/secrets"
DEFAULT_COLLECTION = "/org/freedesktop/secrets/aliases/default"
_SERVICE = "org.freedesktop.Secret.Service"
_PROMPT = "org.freedesktop.Secret.Prompt"
APP = "ai-linux"
CALL_MS = 10_000
PROMPT_S = 120  # the user types the keyring password


class KeyringError(Exception):
    pass


def _bus() -> Gio.DBusConnection:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as exc:
        raise KeyringError(f"no session bus: {exc.message}") from exc


def _call(bus, path: str, iface: str, method: str, args: GLib.Variant | None, reply: str):
    try:
        return bus.call_sync(BUS_NAME, path, iface, method, args, GLib.VariantType(reply),
                             Gio.DBusCallFlags.NONE, CALL_MS, None).unpack()
    except GLib.Error as exc:
        raise KeyringError(exc.message) from exc


def _prompt(bus, path: str) -> GLib.Variant | None:
    """Shows the keyring's own dialog and waits for it; None when the user dismissed it."""
    if path == "/":
        return None
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()  # the Completed signal is delivered here, not on the GTK main loop
    done: dict = {}
    sub = bus.signal_subscribe(BUS_NAME, _PROMPT, "Completed", path, None, Gio.DBusSignalFlags.NONE,
                               lambda *a: done.setdefault("result", a[5]))
    try:
        _call(bus, path, _PROMPT, "Prompt", GLib.Variant("(s)", ("",)), "()")
        timer = GLib.timeout_source_new_seconds(PROMPT_S)
        timer.set_callback(lambda *_: done.setdefault("result", None) and False)
        timer.attach(ctx)
        while "result" not in done:
            ctx.iteration(True)
        timer.destroy()
    finally:
        bus.signal_unsubscribe(sub)
        ctx.pop_thread_default()
    result = done["result"]
    if result is None:
        raise KeyringError("the keyring did not answer in time")
    dismissed, value = result.unpack() if isinstance(result, GLib.Variant) else result
    return None if dismissed else value


def _unlock(bus, paths: list[str]) -> None:
    if not paths:
        return
    _unlocked, prompt = _call(bus, SERVICE_PATH, _SERVICE, "Unlock", GLib.Variant("(ao)", (paths,)), "(aoo)")
    if prompt != "/" and _prompt(bus, prompt) is None:
        raise KeyringError("the keyring stayed locked")


def _session(bus) -> str:
    # "plain": the secret crosses only this user's session bus, as with libsecret without gcrypt
    _out, session = _call(bus, SERVICE_PATH, _SERVICE, "OpenSession",
                          GLib.Variant("(sv)", ("plain", GLib.Variant("s", ""))), "(vo)")
    return session


def _attrs(name: str) -> dict[str, str]:
    return {"application": APP, "name": name}


def _find(bus, name: str) -> list[str]:
    unlocked, locked = _call(bus, SERVICE_PATH, _SERVICE, "SearchItems", GLib.Variant("(a{ss})", (_attrs(name),)),
                             "(aoao)")
    _unlock(bus, list(locked))
    return list(unlocked) + list(locked)


def lookup(name: str) -> str | None:
    """The secret stored as ``name``, or None when there is none."""
    bus = _bus()
    items = _find(bus, name)
    if not items:
        return None
    session = _session(bus)
    (secrets,) = _call(bus, SERVICE_PATH, _SERVICE, "GetSecrets", GLib.Variant("(aoo)", (items, session)),
                       "(a{o(oayays)})")
    for path in items:
        if path in secrets:
            _s, _params, value, _type = secrets[path]
            return bytes(value).decode("utf-8", errors="replace")
    return None


def store(name: str, value: str, label: str) -> None:
    """Saves ``value`` as ``name``, replacing an older one."""
    bus = _bus()
    _unlock(bus, [DEFAULT_COLLECTION])
    session = _session(bus)
    props = {"org.freedesktop.Secret.Item.Label": GLib.Variant("s", label),
             "org.freedesktop.Secret.Item.Attributes": GLib.Variant("a{ss}", _attrs(name))}
    secret = (session, b"", value.encode("utf-8"), "text/plain")
    item, prompt = _call(bus, DEFAULT_COLLECTION, "org.freedesktop.Secret.Collection", "CreateItem",
                         GLib.Variant("(a{sv}(oayays)b)", (props, secret, True)), "(oo)")
    if item == "/" and _prompt(bus, prompt) is None:
        raise KeyringError("saving to the keyring was cancelled")


def clear(name: str) -> int:
    """Deletes every secret stored as ``name``; returns how many."""
    bus = _bus()
    items = _find(bus, name)
    for path in items:
        (prompt,) = _call(bus, path, "org.freedesktop.Secret.Item", "Delete", None, "(o)")
        _prompt(bus, prompt)
    return len(items)


def available() -> bool:
    """A Secret Service is running (or can be started) on the session bus."""
    try:
        bus = _bus()
        (names,) = _call_bus(bus, "ListActivatableNames")
        (running,) = _call_bus(bus, "ListNames")
    except (KeyringError, GLib.Error):
        return False
    return BUS_NAME in names or BUS_NAME in running


def _call_bus(bus, method: str):
    return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", method, None,
                         GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, CALL_MS, None).unpack()
