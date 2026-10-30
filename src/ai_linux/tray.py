"""Top-bar tray icon: a StatusNotifierItem plus a com.canonical.dbusmenu menu over plain Gio D-Bus.

Works with Ubuntu's AppIndicator extension and KDE. Stock GNOME has no watcher, so ``available``
stays False there and the app keeps working without a tray.
"""

from __future__ import annotations

import functools

import itertools
import logging
import math
import os
import sys
import time

import cairo
from gi.repository import Gio, GLib, GObject

from ai_linux.config import APP_NAME, ASSETS_DIR
from ai_linux.state import ACTIVE_STATES, STATE_LABELS, AssistantState
from ai_linux.ui.bar_model import GEOMETRY, N_BARS, TAPER, BarAnimator, Frame, fire_colors, hex_to_rgb

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
ITEM_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

# 16 is the GNOME panel size (the extension picks the smallest pixmap >= its size); 22 KDE; 32 HiDPI.
ICON_SIZES = (16, 22, 32)
ANIMATED_STATES = frozenset({"listening", "thinking", "speaking"})
# Named symbolic icons (scripts/gen_tray_icons.py) take the panel's text colour; see icon_name().
ACTIVE_STATES_TRAY = frozenset({"followup", "listening", "thinking", "speaking"})
WAVE_FRAMES = 6
WAVE_FPS = 5  # the shell batches icon updates near 8 Hz and pays CPU per frame

_ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconAccessibleDesc" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="AttentionAccessibleDesc" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="XAyatanaSecondaryActivate"><arg name="timestamp" type="u" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <method name="ProvideXdgActivationToken"><arg name="token" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="NewMenu"/>
    <signal name="XAyatanaNewLabel"><arg name="label" type="s"/><arg name="guide" type="s"/></signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""

# Menu item ids; 0 is the root.
M_OPEN, M_LISTEN, M_MUTE, M_MODE, M_WAKE, M_HOTKEY, M_ALWAYS, M_SEP, M_PREFS, M_QUIT = range(1, 11)
MENU_TREE = {0: (M_OPEN, M_LISTEN, M_MUTE, M_MODE, M_SEP, M_PREFS, M_QUIT), M_MODE: (M_WAKE, M_HOTKEY, M_ALWAYS)}
RADIO_MODES = {M_WAKE: ("wake", "Wake word"), M_HOTKEY: ("hotkey", "Hotkey only"), M_ALWAYS: ("always", "Always on")}
_PARENT = {child: parent for parent, kids in MENU_TREE.items() for child in kids}

_instance_ids = itertools.count(1)


# ---- icon rendering (pure, unit-tested) ----

def _bar_metrics(size: int) -> tuple[float, float]:
    """Bar width and gap for an icon of ``size`` px: whole pixels when 11 bars fit, else scaled."""
    geo = GEOMETRY["tray"]
    scale = size / geo.width
    bar_w, gap = max(1, round(geo.bar_w * scale)), max(1, round(geo.gap * scale))
    if N_BARS * bar_w + (N_BARS - 1) * gap <= size:
        return float(bar_w), float(gap)
    return geo.bar_w * scale, geo.gap * scale  # 16 px: the 1 px bar / 0.5 px gap of the symbolic icon


def render_frame(frame: Frame, size: int) -> bytes:
    """Draw one bar frame as SNI pixel data (ARGB32, network byte order, straight alpha)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    geo = GEOMETRY["tray"]
    scale = size / geo.width
    bar_w, gap = _bar_metrics(size)
    total = N_BARS * bar_w + (N_BARS - 1) * gap
    x0 = (size - total) / 2
    if bar_w.is_integer():
        x0 = float(int(x0))  # keep whole-pixel bars on the pixel grid
    min_h, max_h = geo.min_h * scale, geo.max_h * scale
    per_bar = fire_colors(dark=True) if frame.fire else (frame.color,) * len(frame.heights)  # the top bar is dark
    for i, (height, alpha, color) in enumerate(zip(frame.heights, frame.opacities, per_bar)):
        h = min_h + (max_h - min_h) * min(1.0, max(0.0, height))
        # Same parity as the icon so the bar centres on whole pixels and stays crisp.
        h = max(2.0, 2 * round((h - size % 2) / 2) + size % 2)
        x, y = x0 + i * (bar_w + gap), (size - h) / 2
        _rounded_bar(cr, x, y, bar_w, h)
        cr.set_source_rgba(*hex_to_rgb(color), max(0.0, min(1.0, alpha)))
        cr.fill()
    surface.flush()
    return cairo_to_sni(surface.get_data(), size, size, surface.get_stride())


def _rounded_bar(cr, x: float, y: float, w: float, h: float) -> None:
    rad = min(w, h) / 2
    cr.new_sub_path()
    cr.arc(x + w - rad, y + rad, rad, -math.pi / 2, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, math.pi / 2)
    cr.arc(x + rad, y + h - rad, rad, math.pi / 2, math.pi)
    cr.arc(x + rad, y + rad, rad, math.pi, 1.5 * math.pi)
    cr.close_path()


def cairo_to_sni(data, width: int, height: int, stride: int) -> bytes:
    """Cairo's native-endian premultiplied ARGB32 to SNI's big-endian straight-alpha ARGB32."""
    src = bytes(data)
    row = width * 4
    if stride != row:
        src = b"".join(src[y * stride:y * stride + row] for y in range(height))
    else:
        src = src[:row * height]
    if sys.byteorder == "little":
        b, g, r, a = src[0::4], src[1::4], src[2::4], src[3::4]
    else:
        a, r, g, b = src[0::4], src[1::4], src[2::4], src[3::4]
    out = bytearray(len(src))
    out[0::4], out[1::4], out[2::4], out[3::4] = a, r, g, b
    # Only antialiased edge pixels need un-premultiplying; opaque and empty ones are already right.
    for i, av in enumerate(a):
        if 0 < av < 255:
            o = 4 * i
            half = av // 2
            out[o + 1] = min(255, (out[o + 1] * 255 + half) // av)
            out[o + 2] = min(255, (out[o + 2] * 255 + half) // av)
            out[o + 3] = min(255, (out[o + 3] * 255 + half) // av)
    return bytes(out)


def pixmap_variant(images: list[tuple[int, int, bytes]]) -> GLib.Variant:
    """Build a(iiay) without per-byte Python conversion (the tuple constructor costs ~36 ms)."""
    items = [
        GLib.Variant.new_tuple(
            GLib.Variant.new_int32(w), GLib.Variant.new_int32(h),
            GLib.Variant.new_from_bytes(GLib.VariantType("ay"), GLib.Bytes(data), True),
        )
        for w, h, data in images
    ]
    return GLib.Variant.new_array(GLib.VariantType("(iiay)"), items)


def icon_name(state: str, tick: int | None = None) -> str:
    """The top-bar icon for a state; ``tick`` steps the wave while it works (None: the still icon)."""
    if state in ANIMATED_STATES and tick is not None:
        return f"phoenix-tray-wave{tick % WAVE_FRAMES}-symbolic"
    if state in ACTIVE_STATES_TRAY:
        return "phoenix-tray-active-symbolic"
    return "phoenix-tray-muted-symbolic" if state == "muted" else "phoenix-tray-idle-symbolic"


def static_frame(state: str) -> Frame:
    """One resting frame per state: the logo silhouette in the state colour (muted: flat dots)."""
    anim = BarAnimator(reduced_motion=True)
    anim.set_state(state, 0.0)
    frame = anim.step(10.0, 0.0)
    if state != "muted":
        frame.heights = list(TAPER)  # the animator's resting pose is too flat to read at 16 px
    return frame


@functools.lru_cache(maxsize=8)
def _static_pixels(state: str) -> tuple[bytes, ...]:
    """The fallback pixmap, drawn once per state: the shell shows the named icon instead."""
    return tuple(render_frame(static_frame(state), s) for s in ICON_SIZES)


class Tray(GObject.Object):
    """StatusNotifierItem for the top bar. ``available`` turns True once a watcher accepted it."""

    available = GObject.Property(type=bool, default=False)

    def __init__(self, ctx, item_id: str = "ai-linux", connection: Gio.DBusConnection | None = None):
        super().__init__()
        self.ctx = ctx
        self.item_id = item_id
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-{next(_instance_ids)}"
        self._conn: Gio.DBusConnection | None = None
        self._reg_ids: list[int] = []
        self._own_id = 0
        self._watch_id = 0
        self._anim_id = 0
        self._closed = False
        self._token: str | None = None
        self._pixmaps: GLib.Variant = pixmap_variant([])  # for hosts that can't load the named icon
        self._last_pixels: tuple[bytes, ...] | None = None
        self._icon = icon_name(self._state())
        self._wave = 0
        self._last_mode = self._non_off_mode(ctx.settings.get("listen_mode"))
        self._animator = BarAnimator(reduced_motion=not _animations_enabled())
        self._animator.set_state(self._state(), time.monotonic())
        self._handlers = [
            (ctx.bus, ctx.bus.connect("state-changed", self._on_state)),
            (ctx.settings, ctx.settings.connect("changed::listen_mode", self._on_listen_mode)),
            (ctx.settings, ctx.settings.connect("changed::muted", self._on_listen_mode)),
            (ctx.settings, ctx.settings.connect("changed::wake_word", lambda *_: self._emit_item("NewToolTip"))),
            (ctx.settings, ctx.settings.connect("changed::hotkey", lambda *_: self._emit_item("NewToolTip"))),
        ]
        self._draw_static()
        try:
            conn = connection or _app_connection(ctx.app)
            if conn is not None:
                self._start(conn)
            else:
                Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)
        except Exception:
            log.exception("tray: could not reach the session bus")

    # ---- lifecycle ----

    def _on_bus(self, _src, res) -> None:
        try:
            conn = Gio.bus_get_finish(res)
        except GLib.Error as exc:
            log.warning("tray: no session bus: %s", exc.message)
            return
        if not self._closed:
            self._start(conn)

    def _start(self, conn: Gio.DBusConnection) -> None:
        self._conn = conn
        item_info = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
        self._reg_ids = [
            conn.register_object(ITEM_PATH, item_info, self._item_call, self._item_prop, None),
            conn.register_object(MENU_PATH, menu_info, self._menu_call, self._menu_prop, None),
        ]
        self._own_id = Gio.bus_own_name_on_connection(
            conn, self.bus_name, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, self._on_name_acquired, self._on_name_lost
        )

    def _on_name_acquired(self, conn, name) -> None:
        if self._closed or self._watch_id:
            return
        self._watch_id = Gio.bus_watch_name_on_connection(
            conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE, self._on_watcher_appeared, self._on_watcher_vanished
        )

    def _on_name_lost(self, _conn, name) -> None:
        if not self._closed:
            log.warning("tray: could not own %s", name)
            self.available = False

    def _on_watcher_appeared(self, conn, _name, owner) -> None:
        if self._closed:
            return
        conn.call(
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.bus_name,)), None, Gio.DBusCallFlags.NONE, 5000, None, self._on_registered,
        )

    def _on_registered(self, conn, res) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            log.warning("tray: watcher refused the item: %s", exc.message)
            self.available = False
            return
        if not self._closed:
            log.info("tray: registered %s", self.bus_name)
            self.available = True

    def _on_watcher_vanished(self, _conn, _name) -> None:
        if self._closed:
            return
        if self.available:
            log.info("tray: StatusNotifierWatcher went away; will re-register when it returns")
        else:
            log.info("tray: no StatusNotifierWatcher on this desktop (GNOME needs the AppIndicator extension)")
        self.available = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_anim()
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._own_id:
            Gio.bus_unown_name(self._own_id)  # the watcher drops the item when the name goes
            self._own_id = 0
        if self._conn is not None:
            for rid in self._reg_ids:
                self._conn.unregister_object(rid)
        self._reg_ids.clear()
        self.available = False

    # ---- state and icon ----

    def _state(self) -> str:
        state = getattr(self.ctx.bus, "state", AssistantState.OFF)
        return state.value if isinstance(state, AssistantState) else str(state)

    def _on_state(self, _bus, state: str, _detail: str) -> None:
        try:
            self._animator.set_state(state, time.monotonic())
            if state in ANIMATED_STATES and self._animator.fps() > 0:
                if not self._anim_id:
                    self._tick()
                    self._anim_id = GLib.timeout_add(1000 // WAVE_FPS, self._tick)
            else:
                self._stop_anim()
                self._set_icon(icon_name(state))
            self._draw_static()
            self._emit_item("NewToolTip")
            self._menu_changed((M_LISTEN,))
        except Exception:
            log.exception("tray: state update failed")

    def _stop_anim(self) -> None:
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = 0

    def _tick(self) -> bool:
        try:
            self._wave += 1
            self._set_icon(icon_name(self._state(), self._wave))
        except Exception:
            log.exception("tray: frame failed")
            self._anim_id = 0
            return False
        return True

    def _set_icon(self, name: str) -> None:
        if name != self._icon:
            self._icon = name
            self._emit_item("NewIcon")

    def _draw_static(self) -> None:
        self._set_pixels(_static_pixels(self._state()))

    def _set_frame(self, frame: Frame) -> None:
        self._set_pixels(tuple(render_frame(frame, s) for s in ICON_SIZES))

    def _set_pixels(self, pixels: tuple[bytes, ...]) -> None:
        if pixels == self._last_pixels:
            return  # a held pose: skip the shell's redraw
        self._last_pixels = pixels
        self._pixmaps = pixmap_variant([(s, s, p) for s, p in zip(ICON_SIZES, pixels)])
        self._emit_item("NewIcon")

    def _tooltip(self) -> tuple[str, str]:
        st = self._state()
        try:
            label = STATE_LABELS[AssistantState(st)]
        except ValueError:
            label = st.capitalize()
        mode = "off" if self.ctx.settings.get("muted") else self.ctx.settings.get("listen_mode")
        if st == "off":
            detail = getattr(self.ctx.bus, "detail", "") or "Not running"
        elif mode == "off":
            detail = "Listening stopped"
        elif mode == "wake":
            detail = f"Say “{self.ctx.settings.get('wake_word')}” to start"
        elif mode == "hotkey":
            detail = f"Press {self._hotkey_label()} to talk"
        else:
            detail = "Always listening"
        return label, detail

    def _hotkey_label(self) -> str:
        desc = getattr(getattr(self.ctx, "hotkey", None), "trigger_description", None)
        if desc:
            return desc
        try:
            from ai_linux.hotkey import accel_label

            return accel_label(self.ctx.settings.get("hotkey"))
        except Exception:
            return "the hotkey"

    # ---- StatusNotifierItem ----

    def _item_prop(self, _conn, _sender, _path, _iface, name):
        try:
            label, detail = self._tooltip()
            values = {
                "Category": ("s", "ApplicationStatus"),
                "Id": ("s", self.item_id),
                "Title": ("s", APP_NAME),
                "Status": ("s", "Active"),
                "WindowId": ("i", 0),
                "IconName": ("s", self._icon),
                "IconThemePath": ("s", str(ASSETS_DIR / "icons")),
                "IconAccessibleDesc": ("s", f"{APP_NAME}: {label}"),
                "OverlayIconName": ("s", ""),
                "AttentionIconName": ("s", ""),
                "AttentionMovieName": ("s", ""),
                "AttentionAccessibleDesc": ("s", ""),
                "ToolTip": ("(sa(iiay)ss)", ("", [], f"{APP_NAME}: {label}", detail)),
                "ItemIsMenu": ("b", False),
                "Menu": ("o", MENU_PATH),
                "XAyatanaLabel": ("s", ""),
                "XAyatanaLabelGuide": ("s", ""),
            }
            if name == "IconPixmap":
                return self._pixmaps
            if name in ("OverlayIconPixmap", "AttentionIconPixmap"):
                return GLib.Variant("a(iiay)", [])
            sig, value = values[name]
            return GLib.Variant(sig, value)
        except Exception:
            log.exception("tray: property %s", name)
            return None

    def _item_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        try:
            if method == "Activate":
                token, self._token = self._token, None
                GLib.idle_add(self._run, self._show_window, token)
            elif method in ("SecondaryActivate", "XAyatanaSecondaryActivate"):
                GLib.idle_add(self._run, self.ctx.controller.toggle_listening)
            elif method == "ProvideXdgActivationToken":
                self._token = params.unpack()[0] or None
            elif method not in ("ContextMenu", "Scroll"):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(None)
        except Exception as exc:
            log.exception("tray: %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _emit_item(self, signal: str) -> None:
        if self._conn is None or self._closed or not self._reg_ids:
            return
        try:
            self._conn.emit_signal(None, ITEM_PATH, ITEM_IFACE, signal, None)
        except GLib.Error as exc:
            log.debug("tray: emit %s: %s", signal, exc.message)

    @staticmethod
    def _run(fn, *args) -> bool:
        try:
            fn(*args)
        except Exception:
            log.exception("tray: action failed")
        return False

    def _show_window(self, token: str | None = None) -> None:
        app = self.ctx.app
        if token and hasattr(app, "get_window"):
            win = app.get_window()
            if win is not None:
                win.set_startup_id(token)  # Wayland grants focus only with the shell's activation token
        app.activate()

    # ---- com.canonical.dbusmenu ----

    @staticmethod
    def _non_off_mode(mode: str) -> str:
        return mode if mode in ("wake", "hotkey", "always") else "wake"

    def _item_props(self, item_id: int) -> dict[str, GLib.Variant]:
        s = GLib.Variant
        if item_id == 0:
            return {"children-display": s("s", "submenu")}
        if item_id == M_OPEN:
            return {"label": s("s", f"Open {APP_NAME}")}
        if item_id == M_LISTEN:
            active = self._state() in _ACTIVE_VALUES
            enabled = bool(getattr(self.ctx.controller, "engine_available", True))
            return {"label": s("s", "Cancel" if active else "Talk Now"), "enabled": s("b", enabled)}
        if item_id == M_MUTE:
            muted = self.ctx.controller.is_muted()
            return {"label": s("s", "Stop Listening"), "toggle-type": s("s", "checkmark"),
                    "toggle-state": s("i", int(muted))}
        if item_id == M_MODE:
            return {"label": s("s", "Listening mode"), "children-display": s("s", "submenu")}
        if item_id in RADIO_MODES:
            mode, label = RADIO_MODES[item_id]
            on = mode == self._last_mode
            return {"label": s("s", label), "toggle-type": s("s", "radio"), "toggle-state": s("i", int(on))}
        if item_id == M_SEP:
            return {"type": s("s", "separator")}
        if item_id == M_PREFS:
            return {"label": s("s", "Preferences")}
        if item_id == M_QUIT:
            return {"label": s("s", "Quit")}
        raise KeyError(item_id)

    def _filtered(self, item_id: int, names) -> dict[str, GLib.Variant]:
        props = self._item_props(item_id)
        return {k: v for k, v in props.items() if k in names} if names else props

    def _layout(self, item_id: int, depth: int, names) -> tuple:
        kids = []
        if depth != 0:
            for child in MENU_TREE.get(item_id, ()):
                node = self._layout(child, depth - 1 if depth > 0 else -1, names)
                kids.append(GLib.Variant("(ia{sv}av)", node))
        return item_id, self._filtered(item_id, names), kids

    def _menu_prop(self, _conn, _sender, _path, _iface, name):
        values = {"Version": ("u", 3), "TextDirection": ("s", "ltr"), "Status": ("s", "normal"),
                  "IconThemePath": ("as", [])}
        if name not in values:
            return None
        return GLib.Variant(*values[name])

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        try:
            invocation.return_value(self._menu_method(method, params.unpack()))
        except KeyError as exc:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", f"unknown menu item {exc}")
        except Exception as exc:
            log.exception("tray: menu %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _menu_method(self, method: str, args: tuple) -> GLib.Variant | None:
        known = set(_PARENT) | {0}
        if method == "GetLayout":
            parent, depth, names = args
            if parent not in known:
                raise KeyError(parent)
            return GLib.Variant("(u(ia{sv}av))", (1, self._layout(parent, depth, names)))
        if method == "GetGroupProperties":
            ids, names = args
            ids = [i for i in (ids or sorted(known)) if i in known]
            return GLib.Variant("(a(ia{sv}))", ([(i, self._filtered(i, names)) for i in ids],))
        if method == "GetProperty":
            item_id, name = args
            props = self._item_props(item_id)
            if name not in props:
                raise KeyError(f"{item_id}.{name}")
            return GLib.Variant("(v)", (props[name],))
        if method == "Event":
            item_id, event, _data, _ts = args
            if item_id not in known:
                raise KeyError(item_id)
            if event == "clicked":
                GLib.idle_add(self._run, self._clicked, item_id)
            return None
        if method == "EventGroup":
            errors = []
            for item_id, event, _data, _ts in args[0]:
                if item_id not in known:
                    errors.append(item_id)
                elif event == "clicked":
                    GLib.idle_add(self._run, self._clicked, item_id)
            return GLib.Variant("(ai)", (errors,))
        if method == "AboutToShow":
            if args[0] not in known:
                raise KeyError(args[0])
            return GLib.Variant("(b)", (False,))  # every change is pushed with ItemsPropertiesUpdated
        if method == "AboutToShowGroup":
            return GLib.Variant("(aiai)", ([], [i for i in args[0] if i not in known]))
        raise NotImplementedError(method)

    def _clicked(self, item_id: int) -> None:
        c = self.ctx.controller
        if item_id == M_OPEN:
            token, self._token = self._token, None
            self._show_window(token)
        elif item_id == M_LISTEN:
            c.toggle_listening()
        elif item_id == M_MUTE:
            c.mute(not c.is_muted())
        elif item_id in RADIO_MODES:
            c.set_listen_mode(RADIO_MODES[item_id][0])
        elif item_id == M_PREFS:
            self.ctx.app.activate_action("preferences", None)
        elif item_id == M_QUIT:
            self.ctx.app.activate_action("quit", None)

    def _on_listen_mode(self, *_args) -> None:
        mode = self.ctx.settings.get("listen_mode")
        if mode != "off":
            self._last_mode = mode
        self._menu_changed((M_MUTE, M_WAKE, M_HOTKEY, M_ALWAYS))
        self._emit_item("NewToolTip")

    def _menu_changed(self, ids) -> None:
        if self._conn is None or self._closed or not self._reg_ids:
            return
        try:
            updated = [(i, self._item_props(i)) for i in ids]
            self._conn.emit_signal(None, MENU_PATH, MENU_IFACE, "ItemsPropertiesUpdated",
                                   GLib.Variant("(a(ia{sv})a(ias))", (updated, [])))
        except Exception:
            log.exception("tray: menu update failed")


_ACTIVE_VALUES = frozenset(s.value for s in ACTIVE_STATES)


def _app_connection(app) -> Gio.DBusConnection | None:
    getter = getattr(app, "get_dbus_connection", None)
    try:
        return getter() if getter else None
    except Exception:
        return None


def _animations_enabled() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        settings = Gtk.Settings.get_default()
        return settings is None or bool(settings.props.gtk_enable_animations)
    except Exception:
        return True
