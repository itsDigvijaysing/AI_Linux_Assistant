"""Floating pill: a small always-on-top waveform while the assistant listens, thinks or speaks.

GNOME has no layer-shell, so the pill is an override-redirect window on the Xwayland display: the
compositor stacks it above everything, never focuses it and it ignores the pointer. Elsewhere it
falls back to gtk4-layer-shell when that is importable, else it stays off.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import warnings

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from ai_linux.state import ACTIVE_STATES, AssistantState  # noqa: E402
from ai_linux.ui import theme  # noqa: E402

log = logging.getLogger(__name__)

SIZES = {"active": (164, 40), "resting": (64, 20)}
MARGIN = 10  # transparent room around the capsule for its shadow
EDGE_GAP = 24  # capsule distance from the work area edge
LINGER_MS = 1200  # stay up briefly after a turn so the last word does not cut off the bars
SHOW_MS, HIDE_MS = 160, 120
_SCALE_FROM = 0.86
_WIN_W = SIZES["active"][0] + 2 * MARGIN
_WIN_H = SIZES["active"][1] + 2 * MARGIN
_CW_OVERRIDE_REDIRECT = 1 << 9


class _XSetWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
        ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("colormap", ctypes.c_ulong), ("cursor", ctypes.c_ulong),
    ]


class _XWMHints(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_long), ("input", ctypes.c_int), ("initial_state", ctypes.c_int),
        ("icon_pixmap", ctypes.c_ulong), ("icon_window", ctypes.c_ulong), ("icon_x", ctypes.c_int),
        ("icon_y", ctypes.c_int), ("icon_mask", ctypes.c_ulong), ("window_group", ctypes.c_ulong),
    ]


class _Xlib:
    """The few libX11 calls GTK4 no longer wraps, on our own connection to the same X server."""

    def __init__(self, name: str):
        path = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.CDLL(path)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XChangeWindowAttributes.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(_XSetWindowAttributes)]
        lib.XSetWMHints.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XWMHints)]
        lib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        self._lib = lib
        self._dpy = lib.XOpenDisplay(name.encode())
        if not self._dpy:
            raise OSError(f"cannot open X display {name}")

    def make_overlay(self, xid: int) -> None:
        attrs = _XSetWindowAttributes(override_redirect=1)
        self._lib.XChangeWindowAttributes(self._dpy, xid, _CW_OVERRIDE_REDIRECT, ctypes.byref(attrs))
        hints = _XWMHints(flags=1, input=0)  # InputHint false: never ask for keyboard focus
        self._lib.XSetWMHints(self._dpy, xid, ctypes.byref(hints))
        # sync so the server applies this before GTK's own connection maps the window
        self._lib.XSync(self._dpy, 0)

    def move(self, xid: int, x: int, y: int) -> None:
        self._lib.XMoveWindow(self._dpy, xid, int(x), int(y))
        self._lib.XFlush(self._dpy)

    def close(self) -> None:
        if self._dpy:
            self._lib.XCloseDisplay(self._dpy)
            self._dpy = None


class _Stage(Gtk.Widget):
    """Draws its child faded and scaled about the screen-edge anchor, for the show and hide motion."""

    __gtype_name__ = "AilPillStage"

    def __init__(self, child: Gtk.Widget):
        super().__init__(layout_manager=Gtk.BinLayout())
        self.child = child
        child.set_parent(self)
        self.fade = 0.0
        self.scale = _SCALE_FROM
        self.anchor_top = False

    def set_motion(self, fade: float, scale: float) -> None:
        self.fade, self.scale = fade, scale
        self.queue_draw()

    def do_snapshot(self, snapshot) -> None:
        if self.fade <= 0.001:
            return
        w, h = self.get_width(), self.get_height()
        snapshot.push_opacity(min(1.0, self.fade))
        if abs(self.scale - 1.0) > 0.001:
            cy = MARGIN if self.anchor_top else h - MARGIN
            snapshot.translate(_point(w / 2, cy))
            snapshot.scale(self.scale, self.scale)
            snapshot.translate(_point(-w / 2, -cy))
        self.snapshot_child(self.child, snapshot)
        snapshot.pop()

    def release(self) -> None:
        if self.child.get_parent() is self:
            self.child.unparent()


def _point(x: float, y: float) -> Graphene.Point:
    p = Graphene.Point()
    p.init(x, y)
    return p


class Pill:
    """Follows ``ctx.bus`` state and the show_pill, pill_always and pill_position settings itself."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.settings = ctx.settings
        self.available = False
        self.backend: str | None = None  # x11 | layer-shell
        self._xlib: _Xlib | None = None
        self._display: Gdk.Display | None = None
        self._own_display = False
        self._layer = None
        self._win: Gtk.Window | None = None
        self._stage: _Stage | None = None
        self._capsule: Gtk.Box | None = None
        self._bars = None
        self._xid = 0
        self._mode: str | None = None  # what is on screen: active | resting | None
        self._hiding = False
        self._linger = False
        self._linger_id = 0
        self._anim: Adw.TimedAnimation | None = None
        self._handlers: list[tuple[object, int]] = []
        self._closed = False
        try:
            if self._open_x11() or self._open_layer_shell():
                self._build()
                self.available = True
                log.info("pill: using %s", self.backend)
            else:
                log.info("pill: no X11 display and no layer-shell, so the floating indicator is off")
        except Exception:
            log.exception("pill: setup failed, the floating indicator is off")
            self._teardown_display()
            return
        if not self.available:
            return
        bus, s = ctx.bus, self.settings
        self._handlers = [
            (bus, bus.connect("state-changed", self._on_state)),
            (s, s.connect("changed::show_pill", lambda *_: self._update())),
            (s, s.connect("changed::pill_always", lambda *_: self._update())),
            (s, s.connect("changed::pill_position", lambda *_: self._on_position())),
        ]
        monitors = self._display.get_monitors()
        self._handlers.append((monitors, monitors.connect("items-changed", lambda *_: self._place())))
        self._update()

    # backends

    def _open_x11(self) -> bool:
        name = os.environ.get("DISPLAY")
        if not name:
            return False
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
        except (ValueError, ImportError) as exc:
            log.info("pill: GdkX11 unavailable: %s", exc)
            return False
        default = Gdk.Display.get_default()
        if isinstance(default, GdkX11.X11Display) and default.get_name() == name:
            display = default
        else:
            display = Gdk.DisplayManager.get().open_display(name)
            self._own_display = display is not None
        if display is None:
            log.info("pill: cannot open the X display %s", name)
            return False
        self._display = display
        try:
            self._xlib = _Xlib(name)
        except OSError as exc:
            log.info("pill: %s", exc)
            self._teardown_display()
            return False
        self.backend = "x11"
        return True

    def _open_layer_shell(self) -> bool:
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell
        except (ValueError, ImportError):
            return False
        if not Gtk4LayerShell.is_supported():
            log.info("pill: this compositor has no layer-shell")
            return False
        self._layer = Gtk4LayerShell
        self._display = Gdk.Display.get_default()
        self.backend = "layer-shell"
        return True

    # window

    def _build(self) -> None:
        theme.install(self._display)
        theme.apply_color_scheme(self.settings, self._display)
        from ai_linux.ui.bars import WaveBars

        self._bars = WaveBars(self.ctx.bus, "pill")
        self._bars.set_halign(Gtk.Align.CENTER)
        self._bars.set_valign(Gtk.Align.CENTER)
        self._bars.set_hexpand(True)
        self._capsule = Gtk.Box(halign=Gtk.Align.CENTER, css_classes=["ail-pill"])
        self._capsule.append(self._bars)
        self._stage = _Stage(self._capsule)
        win = Gtk.Window(title="AI Linux indicator", decorated=False, resizable=False, focusable=False,
                         can_focus=False, deletable=False, css_classes=["ail-pill-window"])
        if self.backend == "x11":
            win.set_display(self._display)
        win.set_default_size(_WIN_W, _WIN_H)
        win.set_size_request(_WIN_W, _WIN_H)
        win.set_child(self._stage)
        if self.backend == "layer-shell":
            ls = self._layer
            ls.init_for_window(win)
            ls.set_namespace(win, "ai-linux-pill")
            ls.set_layer(win, ls.Layer.OVERLAY)
            ls.set_keyboard_mode(win, ls.KeyboardMode.NONE)
        win.connect("realize", self._on_realize)
        win.connect("map", self._on_map)
        self._win = win
        self._apply_mode("active")
        self._apply_anchor()

    def _on_realize(self, win) -> None:
        if self.backend != "x11":
            return
        from gi.repository import GdkX11

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._xid = GdkX11.X11Surface.get_xid(win.get_surface())
        self._xlib.make_overlay(self._xid)
        self._place()

    def _on_map(self, win) -> None:
        surface = win.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())  # empty input region: clicks fall through
        self._place()

    def _monitor(self):
        display = self._display
        primary = getattr(display, "get_primary_monitor", None)
        mon = primary() if primary else None
        if mon is None:
            monitors = display.get_monitors()
            mon = monitors.get_item(0) if monitors.get_n_items() else None
        return mon

    def _place(self) -> None:
        if self._closed or self._win is None:
            return
        if self.backend == "layer-shell":
            self._place_layer()
            return
        if not self._xid:
            return
        mon = self._monitor()
        if mon is None:
            return
        area = mon.get_geometry()
        workarea = getattr(mon, "get_workarea", None)
        if workarea is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                area = workarea()
        scale = self._win.get_surface().get_scale_factor() if self._win.get_surface() else 1
        x = area.x + (area.width - _WIN_W) // 2
        if self.settings.get("pill_position") == "top":
            y = area.y + EDGE_GAP - MARGIN
        else:
            y = area.y + area.height - EDGE_GAP - _WIN_H + MARGIN
        self._xlib.move(self._xid, x * scale, y * scale)

    def _place_layer(self) -> None:
        ls, win = self._layer, self._win
        top = self.settings.get("pill_position") == "top"
        ls.set_anchor(win, ls.Edge.TOP, top)
        ls.set_anchor(win, ls.Edge.BOTTOM, not top)
        ls.set_margin(win, ls.Edge.TOP if top else ls.Edge.BOTTOM, EDGE_GAP - MARGIN)

    def _apply_anchor(self) -> None:
        top = self.settings.get("pill_position") == "top"
        self._capsule.set_valign(Gtk.Align.START if top else Gtk.Align.END)
        self._capsule.set_margin_top(MARGIN)
        self._capsule.set_margin_bottom(MARGIN)
        self._stage.anchor_top = top

    def _apply_mode(self, mode: str) -> None:
        w, h = SIZES[mode]
        self._capsule.set_size_request(w, h)
        self._bars.set_geometry("pill" if mode == "active" else "resting")

    def _on_position(self) -> None:
        if self._closed:
            return
        self._apply_anchor()
        self._place()

    # visibility

    def _state(self) -> AssistantState:
        state = getattr(self.ctx.bus, "state", AssistantState.OFF)
        try:
            return AssistantState(state)
        except ValueError:
            return AssistantState.OFF

    def _wanted(self) -> str | None:
        if not self.settings.get("show_pill"):
            return None
        if self._state() in ACTIVE_STATES or self._linger:
            return "active"
        return "resting" if self.settings.get("pill_always") else None

    def _on_state(self, _bus, state: str, _detail: str) -> None:
        try:
            active = AssistantState(state) in ACTIVE_STATES
        except ValueError:
            active = False
        if active:
            self._cancel_linger()
        elif self._mode == "active" and not self._hiding and not self._linger:
            self._linger = True
            self._linger_id = GLib.timeout_add(LINGER_MS, self._on_linger_done)
        self._update()

    def _on_linger_done(self) -> bool:
        self._linger_id = 0
        self._linger = False
        self._update()
        return GLib.SOURCE_REMOVE

    def _cancel_linger(self) -> None:
        if self._linger_id:
            GLib.source_remove(self._linger_id)
            self._linger_id = 0
        self._linger = False

    def _update(self) -> None:
        if self._closed or self._win is None:
            return
        wanted = self._wanted()
        if wanted is None:
            if self._mode is not None and not self._hiding:
                self._hide()
            return
        if self._mode is None or self._hiding:
            self._show(wanted)
        elif wanted != self._mode:
            self._mode = wanted
            self._apply_mode(wanted)
            self._animate(0.0, 1.0, SHOW_MS, scale_only=True)

    def _show(self, mode: str) -> None:
        self._hiding = False
        start = self._stage.fade if self._mode is not None else 0.0
        self._mode = mode
        self._apply_mode(mode)
        if not self._win.get_visible():
            self._stage.set_motion(0.0, _SCALE_FROM)
            self._win.set_visible(True)  # never present(): that would ask for focus
        self._animate(start, 1.0, SHOW_MS)

    def _hide(self) -> None:
        self._hiding = True
        self._animate(self._stage.fade, 0.0, HIDE_MS)

    def _animate(self, start: float, end: float, ms: int, scale_only: bool = False) -> None:
        """Fade and scale between ``start`` and ``end``; ``scale_only`` pops the capsule after a size change."""
        if self._anim is not None:
            if scale_only:
                self._anim.skip()  # finish the previous motion at full opacity
            else:
                self._anim.pause()  # the new motion starts from the current fade
        if scale_only:
            start, end = 0.0, 1.0

        def step(value: float) -> None:
            scale = _SCALE_FROM + (1.0 - _SCALE_FROM) * value
            self._stage.set_motion(1.0 if scale_only else value, scale)

        anim = Adw.TimedAnimation.new(self._stage, start, end, ms, Adw.CallbackAnimationTarget.new(step))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.connect("done", self._on_anim_done)
        self._anim = anim
        anim.play()

    def _on_anim_done(self, anim) -> None:
        if anim is not self._anim:
            return
        self._anim = None
        if self._hiding and self._win is not None:
            self._hiding = False
            self._mode = None
            self._win.set_visible(False)  # unmapped, so fullscreen apps keep direct scanout

    # lifecycle

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_linger()
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        if self._anim is not None:
            self._anim.pause()
            self._anim = None
        if self._win is not None:
            if self._stage is not None:
                self._win.set_child(None)
                self._stage.release()
            self._win.destroy()
            self._win = None
        self._teardown_display()
        self.available = False

    def _teardown_display(self) -> None:
        if self._xlib is not None:
            self._xlib.close()
            self._xlib = None
        if self._own_display and self._display is not None:
            try:
                self._display.close()
            except Exception:
                log.debug("pill: closing the X display", exc_info=True)
        self._display = None
        self._own_display = False


__all__ = ["Pill"]
