"""Floating pill: a small always-on-top waveform while the assistant listens, thinks or speaks.

Above it (below it when the pill sits at the top) a frosted caption shows your words as you speak and grows
into a card with the reply while it is spoken; ``ui.caption_model`` holds its rules and numbers.

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
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from ai_linux.schema import is_big  # noqa: E402
from ai_linux.state import ACTIVE_STATES, AssistantState  # noqa: E402
from ai_linux.ui import caption_model as cm  # noqa: E402
from ai_linux.ui import theme  # noqa: E402

log = logging.getLogger(__name__)

SIZES = {"active": (164, 40), "resting": (64, 20)}
MARGIN = 18  # transparent room around the pill for its shadows
GAP = 8  # between the capsule and the caption
CANVAS_W, CANVAS_H = cm.REPLY_W, 112  # the caption's largest card (4 lines at 14 px, some room for large text)
EDGE_GAP = 24  # capsule distance from the work area edge
LINGER_MS = 1200  # stay up briefly after a turn so the last word does not cut off the bars
SHOW_MS, HIDE_MS = 160, 120
_SCALE_FROM = 0.86
_WIN_W = CANVAS_W + 2 * MARGIN
_WIN_H = CANVAS_H + GAP + SIZES["active"][1] + 2 * MARGIN
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


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def _rgba(rgba: tuple[float, float, float, float], scale: float = 1.0) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = rgba[0], rgba[1], rgba[2], rgba[3] * scale
    return c


def _stops(*pairs: tuple[float, Gdk.RGBA]) -> list[Gsk.ColorStop]:
    out = []
    for offset, color in pairs:
        stop = Gsk.ColorStop()
        stop.offset, stop.color = offset, color
        out.append(stop)
    return out


class _Caption(Gtk.Widget):
    """The live line and the reply card: frosted glass that grows out of the capsule and shrinks back into it."""

    __gtype_name__ = "AilPillCaption"

    def __init__(self):
        super().__init__(css_classes=["ail-pill-caption"], can_target=False)
        self.model = cm.CaptionModel()
        self.anchor_top = False
        self._layout: Pango.Layout | None = None
        self._text_w = self._text_h = 0
        self._draw_mode = "live"  # how the current layout is laid out; kept while the card fades
        self._old: tuple | None = None  # the previous words, fading out while the new ones fade in
        self._shown: str | None = None  # the mode on screen, None once hidden
        cw, ch = SIZES["active"]
        self._rest = {"w": float(cw), "h": float(ch - 12)}  # the size it grows from and shrinks to
        self._cur = {**self._rest, "alpha": 0.0, "text": 0.0, "x": 0.0}
        self._goal = dict(self._cur)
        self._tick = 0
        self._last = 0.0
        self._style = None
        self._style_handler = 0
        self._pages = 0  # bumped to cancel the timers turning a long reply's pages

    # what to show

    def live(self, text: str, stable: int, final: bool) -> None:
        if self.model.live(text, stable, final):
            self._sync()

    def saying(self, text: str, seconds: float = 0.0) -> None:
        """A sentence as it is spoken; one too long for a card turns pages in step with the voice."""
        self._stop_pages()
        cards = cm.pages(" ".join(text.split()), self._fits)
        if self.model.saying(cards[0]):
            self._sync()
        for card, start in list(zip(cards, cm.page_starts(cards, seconds)))[1:]:
            GLib.timeout_add(int(start * 1000), self._turn_page, card, self._pages)

    def _turn_page(self, card: str, run: int) -> bool:
        if run == self._pages and self.model.mode == "reply" and self.model.saying(card):
            self._sync()
        return GLib.SOURCE_REMOVE

    def _stop_pages(self) -> None:
        self._pages += 1

    def _max_lines(self) -> int:
        """At most MAX_LINES, fewer when large text would not fit the canvas."""
        line_h = max(1, self.create_pango_layout("Ag").get_pixel_size()[1])
        return max(1, min(cm.MAX_LINES, (CANVAS_H - 2 * cm.PAD_Y) // line_h))

    def _fits(self, text: str) -> bool:
        layout = self.create_pango_layout(text)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_width(int(cm.REPLY_W - 2 * cm.PAD_X) * Pango.SCALE)
        return layout.get_line_count() <= self._max_lines()

    def on_state(self, state: str) -> None:
        if self.model.state(state):
            self._stop_pages()
            self._sync()

    def clear(self, now: bool = False) -> None:
        self._stop_pages()
        if self.model.hide():
            self._sync()
        if now:
            self._finish()

    def _sync(self) -> None:
        m, g, c = self.model, self._goal, self._cur
        if m.mode == "hidden":
            g.update(self._rest, alpha=0.0)
            self._shown = None
            self._wake()
            return
        layout = self.create_pango_layout(None)
        if m.mode == "live":
            layout.set_markup(cm.live_markup(m.text, m.stable), -1)
            tw, th = layout.get_pixel_size()
            g.update(w=float(cm.LIVE_W), h=float(th + 2 * cm.PAD_Y), x=cm.scroll_target(tw, cm.LIVE_W - 2 * cm.PAD_X))
        else:
            width = self._reply_layout(layout, m.text)
            tw, th = layout.get_pixel_size()
            g.update(w=width, h=float(min(CANVAS_H, th + 2 * cm.PAD_Y)), x=0.0)
        g.update(alpha=1.0, text=1.0)
        if c["alpha"] <= 0.01:
            c.update(self._rest)  # grow out of the capsule
        if self._shown != m.mode or m.mode == "reply":
            if self._layout is not None and c["alpha"] > 0.01 and c["text"] > 0.01:
                self._old = (self._layout, self._text_w, self._text_h, self._draw_mode, c["x"], c["text"])
            c["text"] = 0.0  # new content fades in
        if self._shown != m.mode:
            c["x"] = g["x"]  # no slide in from the old content's place
        self._layout, self._text_w, self._text_h = layout, tw, th
        self._draw_mode = self._shown = m.mode
        self._wake()

    def _reply_layout(self, layout: Pango.Layout, text: str) -> float:
        """Wrap the sentence in even lines; returns the card's width."""
        layout.set_text(text, -1)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_alignment(Pango.Alignment.CENTER)
        width = cm.reply_width(layout.get_pixel_size()[0])
        inner = int(width - 2 * cm.PAD_X)

        def lines_at(px: int) -> int:
            layout.set_width(px * Pango.SCALE)
            return layout.get_line_count()

        most = self._max_lines()
        if lines_at(inner) <= most:
            inner = cm.balanced_width(lines_at, inner, inner // 2)
            width = max(float(cm.LIVE_W), float(inner + 2 * cm.PAD_X))
        layout.set_width(inner * Pango.SCALE)
        layout.set_height(-most)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        return width

    # motion

    def _animations(self) -> bool:
        settings = self.get_settings()
        return settings is None or bool(settings.props.gtk_enable_animations)

    def _wake(self) -> None:
        if not self.get_mapped() or not self._animations():
            self._finish()
            return
        if not self._tick:
            self._last = 0.0
            self._tick = self.add_tick_callback(self._on_tick)

    def _finish(self) -> None:
        self._stop_tick()
        self._cur.update(self._goal)
        self._old = None
        if self._goal["alpha"] <= 0:
            self._layout = None
        self.queue_draw()

    def _stop_tick(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def _on_tick(self, _widget, clock) -> bool:
        now = clock.get_frame_time() / 1e6
        dt = min(0.05, now - self._last) if self._last else 1 / 60
        self._last = now
        c, g = self._cur, self._goal
        c["h"] = max(c["h"], g["h"])  # grow at once: a card smaller than its words clips the top and bottom lines
        for key in ("w", "h", "x"):
            c[key] = cm.approach(c[key], g[key], dt, cm.TAU_MOVE)
        fade = cm.TAU_FADE_IN if g["alpha"] > 0 else cm.TAU_FADE_OUT
        for key in ("alpha", "text"):
            c[key] = cm.approach(c[key], g[key], dt, fade, snap=0.01)
        if c["text"] >= g["text"]:
            self._old = None
        self.queue_draw()
        if c != g:
            return GLib.SOURCE_CONTINUE
        self._tick = 0
        if g["alpha"] <= 0:
            self._layout = None
        return GLib.SOURCE_REMOVE

    # GtkWidget

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        if self._style is None:
            self._style = Adw.StyleManager.get_for_display(self.get_display())
            self._style_handler = self._style.connect("notify::dark", lambda *_: self.queue_draw())

    def do_unmap(self) -> None:
        self._finish()
        if self._style is not None:
            self._style.disconnect(self._style_handler)
            self._style, self._style_handler = None, 0
        Gtk.Widget.do_unmap(self)

    def do_measure(self, orientation, _for_size):
        size = CANVAS_W if orientation == Gtk.Orientation.HORIZONTAL else CANVAS_H
        return size, size, -1, -1

    def do_snapshot(self, snapshot) -> None:
        c = self._cur
        if c["alpha"] <= 0.005:
            return
        try:
            self._draw(snapshot, c)
        except Exception:
            log.exception("drawing the caption failed")

    def _draw(self, snapshot, c: dict) -> None:
        glass = cm.GLASS[bool(self._style is not None and self._style.get_dark())]
        cw, ch = self.get_width(), self.get_height()
        w, h = min(c["w"], cw), min(c["h"], ch)
        x0 = (cw - w) / 2
        y0 = 0.0 if self.anchor_top else ch - h
        rect = _rect(x0, y0, w, h)
        outline = Gsk.RoundedRect()
        outline.init_from_rect(rect, min(cm.RADIUS, h / 2))
        snapshot.push_opacity(c["alpha"])
        snapshot.append_outset_shadow(outline, _rgba(glass["shadow"]), 0, 8, -4, 22)
        snapshot.append_outset_shadow(outline, _rgba(glass["shadow"], 0.4), 0, 1, 0, 3)
        snapshot.push_rounded_clip(outline)
        snapshot.append_color(_rgba(glass["fill"]), rect)
        sheen = _rgba(glass["sheen"])
        snapshot.append_linear_gradient(rect, _point(x0, y0), _point(x0, y0 + min(h, 44)),
                                        _stops((0.0, sheen), (1.0, _rgba(glass["sheen"], 0.0))))
        fade_in = c["text"]
        if self._old is not None:  # out in the first half, then the new words come in: never both at once
            layout, tw, th, mode, x, alpha = self._old
            self._draw_text(snapshot, glass, (x0, y0, w, h), layout, tw, th, mode, x, alpha * max(0.0, 1 - 2 * fade_in))
            fade_in = max(0.0, 2 * fade_in - 1)
        if self._layout is not None:
            self._draw_text(snapshot, glass, (x0, y0, w, h), self._layout, self._text_w, self._text_h,
                            self._draw_mode, c["x"], fade_in)
        snapshot.pop()
        snapshot.append_inset_shadow(outline, _rgba(glass["edge"]), 0, 1, 0, 0)
        snapshot.append_border(outline, [1.0] * 4, [_rgba(glass["border"])] * 4)
        snapshot.pop()

    @staticmethod
    def _draw_text(snapshot, glass: dict, card: tuple, layout: Pango.Layout, tw: float, th: float, mode: str,
                   x: float, opacity: float) -> None:
        if opacity <= 0.005:
            return
        x0, y0, w, h = card
        color = _rgba(glass["text"])
        cx, ty = x0 + w / 2, y0 + (h - th) / 2
        snapshot.push_opacity(opacity)
        if mode == "live":
            box = cm.LIVE_W - 2 * cm.PAD_X
            bx = cx - box / 2
            left, right = cm.edge_fades(x, tw, box)
            f = min(0.5, cm.FADE / box)
            ink = (0.0, 0.0, 0.0, 1.0)
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            snapshot.append_linear_gradient(
                _rect(bx, y0, box, h), _point(bx, 0), _point(bx + box, 0),
                _stops((0.0, _rgba(ink, 1 - left)), (f, _rgba(ink)), (1 - f, _rgba(ink)), (1.0, _rgba(ink, 1 - right))))
            snapshot.pop()
            snapshot.save()
            snapshot.translate(_point(bx + x, ty))
            snapshot.append_layout(layout, color)
            snapshot.restore()
            snapshot.pop()
        else:
            snapshot.save()
            snapshot.translate(_point(cx - layout.get_width() / Pango.SCALE / 2, ty))
            snapshot.append_layout(layout, color)
            snapshot.restore()
        snapshot.pop()


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
        self._column: Gtk.Box | None = None
        self._caption: _Caption | None = None
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
        style = Adw.StyleManager.get_for_display(self._display)
        self._handlers = [
            (bus, bus.connect("state-changed", self._on_state)),
            (bus, bus.connect("live-text", self._on_live_text)),
            (bus, bus.connect("saying", self._on_saying)),
            (s, s.connect("changed::show_pill", lambda *_: self._update())),
            (s, s.connect("changed::pill_always", lambda *_: self._update())),
            (s, s.connect("changed::pill_position", lambda *_: self._on_position())),
            (s, s.connect("changed::live_text", lambda *_: s.get("live_text") or self._caption.clear())),
            # the pill's own light/dark: a CSS media query does not follow a forced app theme
            (style, style.connect("notify::dark", lambda *_: self._on_dark(style))),
        ]
        self._on_dark(style)
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
        self._caption = _Caption()
        self._caption.set_halign(Gtk.Align.CENTER)
        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, halign=Gtk.Align.CENTER)
        for side in ("top", "bottom", "start", "end"):
            getattr(self._column, f"set_margin_{side}")(MARGIN)
        self._column.append(self._caption)
        self._column.append(self._capsule)
        self._stage = _Stage(self._column)
        win = Gtk.Window(title="Phoenix indicator", decorated=False, resizable=False, focusable=False,
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
        self._column.set_valign(Gtk.Align.START if top else Gtk.Align.END)
        # the caption sits on the screen side away from the edge, so it grows toward the middle
        self._column.reorder_child_after(self._capsule, None if top else self._caption)
        self._caption.anchor_top = top
        self._stage.anchor_top = top

    def _on_dark(self, style) -> None:
        if self._capsule is not None:
            (self._capsule.add_css_class if style.get_dark() else self._capsule.remove_css_class)("ail-dark")

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
        if self._state() in ACTIVE_STATES or self._state() == AssistantState.LOADING or self._linger:
            return "active"
        return "resting" if self.settings.get("pill_always") else None

    def _on_live_text(self, _bus, text: str, stable: int, final: bool) -> None:
        if self.settings.get("live_text"):
            self._caption.live(text, stable, final)

    def _on_saying(self, _bus, text: str, seconds: float) -> None:
        if self.settings.get("live_text"):
            self._caption.saying(text, seconds)

    def _on_state(self, _bus, state: str, detail: str) -> None:
        self._caption.on_state(state)
        if state == "thinking" and is_big(detail):
            self._capsule.add_css_class("ail-big")  # the big model's weight: an ember glow, kept while it speaks
        elif state not in ("thinking", "speaking"):
            self._capsule.remove_css_class("ail-big")
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
            self._caption.clear(now=True)  # the next show starts without old words
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
