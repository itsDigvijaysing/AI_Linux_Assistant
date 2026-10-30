"""Waveform indicator: 11 tapered bars driven by ui.bar_model (window hero, floating pill, mic test).

Ticks only while the model is animating and at the model's frame rate, so an idle window costs
nothing and a 144 Hz monitor does not triple the work.
"""

from __future__ import annotations

import logging
import math
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from ai_linux.schema import is_big  # noqa: E402
from ai_linux.state import STATE_LABELS, AssistantState  # noqa: E402
from ai_linux.ui.bar_model import COLORS, GEOMETRY, QUIET, BarAnimator, Frame, hex_to_rgb  # noqa: E402

log = logging.getLogger(__name__)

FOLLOWUP_WINDOW_S = 30.0  # the engine's wake window; a shorter one seen live is used instead
_PAD = {"hero": 12, "row": 8, "pill": 4, "resting": 2, "tray": 0}  # room for the shake and the progress line
_STALE_LEVEL_S = 0.5  # a writer that stopped pushing reads as silence
_QUIET_POLL_MS = 80  # wake check while a silent listening state is parked
_PROGRESS_FPS = 10  # progress line refresh when the bars themselves are still
_SLACK_S = 0.004  # frame-time jitter tolerated before a tick counts as early


class WaveBars(Gtk.Widget):
    """Bars for one ``bar_model.GEOMETRY`` key.

    With ``bus`` it follows ``state-changed`` and reads ``bus.level`` per frame. Without it, drive it
    with ``set_state`` and ``set_level_source`` (a callable returning 0..1).
    """

    __gtype_name__ = "AilWaveBars"

    def __init__(self, bus=None, geometry: str = "hero"):
        super().__init__(accessible_role=Gtk.AccessibleRole.IMG)
        self._bus = bus
        self._bus_handler = 0
        self._geo_name = geometry if geometry in GEOMETRY else "hero"
        if self._geo_name != geometry:
            log.warning("unknown bar geometry %r, using hero", geometry)
        self._geo = GEOMETRY[self._geo_name]
        self._system_reduced = False
        self._anim = BarAnimator()
        self._state = "idle"
        self._level_fn = None
        self._progress_fn = None
        self._level_fn_failed = False
        self._progress_fn_failed = False
        self._frame: Frame | None = None
        self._tick_id = 0
        self._poll_id = 0
        self._next_due = 0.0
        self._settings = None
        self._settings_handler = 0
        self._style = None
        self._style_handler = 0
        self._followup_span = FOLLOWUP_WINDOW_S
        self._followup_deadline: float | None = None
        self._live_followup = False
        self._rgba_cache: dict[tuple[str, float], Gdk.RGBA] = {}
        self.add_css_class("ail-wavebars")
        self._apply_reduced_motion()
        initial = bus.state if bus is not None else AssistantState.IDLE
        self._set_state(str(getattr(initial, "value", initial)), live=False)

    # public API

    def set_state(self, state) -> None:
        """Set the state directly (used when there is no bus)."""
        self._set_state(str(getattr(state, "value", state)), live=True)

    def set_level_source(self, fn) -> None:
        """``fn()`` returns the current level 0..1, or pass None to fall back to the bus."""
        self._level_fn = fn
        self._level_fn_failed = False
        self._wake()

    def set_progress_source(self, fn) -> None:
        """``fn()`` returns follow-up time left 0..1; None computes it from ``bus.followup_until``."""
        self._progress_fn = fn
        self._progress_fn_failed = False
        self._wake()

    def set_geometry(self, geometry: str) -> None:
        if geometry == self._geo_name or geometry not in GEOMETRY:
            return
        self._geo_name, self._geo = geometry, GEOMETRY[geometry]
        self._apply_reduced_motion()
        self.queue_resize()
        self._frame = None
        self._wake()

    @property
    def state(self) -> str:
        return self._state

    @property
    def geometry(self) -> str:
        return self._geo_name

    # state

    def _set_state(self, state: str, live: bool) -> None:
        if state == self._state and self._frame is not None:
            return
        self._state = state
        self._anim.set_state(state, time.monotonic())
        if state == "followup":
            self._followup_deadline = None
            self._followup_span = FOLLOWUP_WINDOW_S
            self._live_followup = live
        try:
            label = STATE_LABELS.get(AssistantState("thinking" if state == "big" else state), state.title())
        except ValueError:
            label = state.title()
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self._wake()

    def _on_bus_state(self, _bus, state: str, detail: str) -> None:
        self._set_state(_shown(state, detail), live=True)

    def _apply_reduced_motion(self) -> None:
        # The resting capsule sits over every app all day, so it never animates.
        reduced = self._system_reduced or self._geo_name == "resting"
        if reduced != self._anim.reduced_motion:
            self._anim.reduced_motion = reduced
            self._wake()

    def _on_animations_changed(self, settings, _pspec) -> None:
        self._system_reduced = not settings.props.gtk_enable_animations
        self._apply_reduced_motion()

    # inputs

    def _level(self) -> float:
        if self._level_fn is not None:
            try:
                return min(1.0, max(0.0, float(self._level_fn())))
            except Exception:
                if not self._level_fn_failed:
                    log.exception("bar level source failed")
                    self._level_fn_failed = True
                return 0.0
        bus = self._bus
        if bus is None or time.monotonic() - getattr(bus, "level_ts", 0.0) > _STALE_LEVEL_S:
            return 0.0
        want = {"listening": "mic", "speaking": "tts"}.get(self._state)
        source = getattr(bus, "level_source", None)
        if want is None or (source is not None and source != want):
            return 0.0
        return min(1.0, max(0.0, float(bus.level)))

    def _progress(self) -> float | None:
        if self._state != "followup":
            return None
        if self._progress_fn is not None:
            try:
                value = self._progress_fn()
                return None if value is None else min(1.0, max(0.0, float(value)))
            except Exception:
                if not self._progress_fn_failed:
                    log.exception("bar progress source failed")
                    self._progress_fn_failed = True
                return None
        until = getattr(self._bus, "followup_until", None) if self._bus is not None else None
        if until is None:
            return None
        remaining = until - time.monotonic()
        if self._followup_deadline is None or until > self._followup_deadline + 0.05:
            # A window seen opening starts full; one joined midway keeps the standard span.
            fresh = self._followup_deadline is not None or self._live_followup
            self._followup_span = max(0.5, remaining) if fresh and remaining < FOLLOWUP_WINDOW_S \
                else FOLLOWUP_WINDOW_S
            self._followup_deadline = until
        return min(1.0, max(0.0, remaining / self._followup_span))

    # animation loop

    def _wake(self) -> None:
        """Start ticking again after a state, level or geometry change."""
        self._stop_poll()
        if self.get_mapped() and not self._tick_id:
            self._next_due = 0.0
            self._tick_id = self.add_tick_callback(self._on_tick)
        self.queue_draw()

    def _stop_tick(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _stop_poll(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _target_fps(self, progress: float | None) -> int:
        fps = self._anim.fps()
        if fps == 0:
            # blends and the shake still need frames; a moving progress line needs a few
            return _PROGRESS_FPS if progress and self._anim.is_settled(time.monotonic()) else 60
        return fps

    def _on_tick(self, _widget, clock) -> bool:
        try:
            now = clock.get_frame_time() / 1e6
            progress = self._progress()
            period = 1.0 / self._target_fps(progress)
            if now < self._next_due - min(_SLACK_S, period * 0.25):
                return GLib.SOURCE_CONTINUE
            # Keep a steady cadence on any refresh rate; resync after a stall.
            self._next_due = self._next_due + period if now - self._next_due < period else now + period
            level = self._level()
            self._anim.dark = self._dark()
            self._frame = self._anim.step(now, level, progress)
            self.queue_draw()
            if self._anim.is_settled(now, level) and not progress:
                self._tick_id = 0
                if self._state == "listening":
                    self._poll_id = GLib.timeout_add(_QUIET_POLL_MS, self._on_quiet_poll)
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        except Exception:
            log.exception("bar animation tick failed")
            self._tick_id = 0
            return GLib.SOURCE_REMOVE

    def _on_quiet_poll(self) -> bool:
        if self._level() < QUIET:
            return GLib.SOURCE_CONTINUE
        self._poll_id = 0
        self._wake()
        return GLib.SOURCE_REMOVE

    # GtkWidget

    def do_root(self) -> None:
        Gtk.Widget.do_root(self)
        if self._bus is not None and not self._bus_handler:
            self._bus_handler = self._bus.connect("state-changed", self._on_bus_state)
            state = str(getattr(self._bus.state, "value", self._bus.state))
            self._set_state(_shown(state, str(getattr(self._bus, "detail", "") or "")), live=False)

    def do_unroot(self) -> None:
        if self._bus_handler:
            self._bus.disconnect(self._bus_handler)
            self._bus_handler = 0
        Gtk.Widget.do_unroot(self)

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        settings = self.get_settings()
        if settings is not None and self._settings is None:
            self._settings = settings
            self._settings_handler = settings.connect("notify::gtk-enable-animations", self._on_animations_changed)
            self._system_reduced = not settings.props.gtk_enable_animations
            self._apply_reduced_motion()
        if self._style is None:
            # per display: the pill lives on its own (X11) display
            self._style = Adw.StyleManager.get_for_display(self.get_display())
            self._style_handler = self._style.connect("notify::dark", lambda *_: self._on_dark())
        self._wake()

    def _dark(self) -> bool:
        return self._style is not None and self._style.get_dark()

    def _on_dark(self) -> None:
        self._frame = None  # the next draw picks the light or dark palette
        self._wake()

    def do_unmap(self) -> None:
        self._stop_tick()
        self._stop_poll()
        if self._settings is not None:
            self._settings.disconnect(self._settings_handler)
            self._settings, self._settings_handler = None, 0
        if self._style is not None:
            self._style.disconnect(self._style_handler)
            self._style, self._style_handler = None, 0
        Gtk.Widget.do_unmap(self)

    def do_measure(self, orientation, _for_size):
        pad = _PAD.get(self._geo_name, 4)
        extent = self._geo.width if orientation == Gtk.Orientation.HORIZONTAL else self._geo.max_h
        size = math.ceil(extent + 2 * pad)
        return size, size, -1, -1

    def do_snapshot(self, snapshot) -> None:
        try:
            self._draw(snapshot)
        except Exception:
            log.exception("drawing bars failed")

    # drawing

    def _rgba(self, color: str, alpha: float) -> Gdk.RGBA:
        key = (color, alpha)
        rgba = self._rgba_cache.get(key)
        if rgba is None:
            if len(self._rgba_cache) > 64:
                self._rgba_cache.clear()
            r, g, b = hex_to_rgb(color)
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b, alpha
            self._rgba_cache[key] = rgba
        return rgba

    def _draw(self, snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        frame = self._frame
        if frame is None:
            self._anim.dark = self._dark()
            frame = self._frame = self._anim.step(time.monotonic(), self._level(), self._progress())
        g = self._geo
        color = frame.color
        per_bar = frame.colors or (color,) * len(frame.heights)
        if self._geo_name == "resting" and self._state in ("idle", "followup"):
            color = COLORS["resting"]  # a quiet light blue over every app all day
            per_bar = (color,) * len(frame.heights)
        # whole pixels horizontally keep the bar edges crisp; heights stay fractional for smooth motion
        x0 = round((width - g.width) / 2 + frame.x_offset)
        cy = height / 2
        radius = g.bar_w / 2
        groups: dict[tuple[float, str], list[tuple[float, float, float]]] = {}
        for i, (h, op, c) in enumerate(zip(frame.heights, frame.opacities, per_bar)):
            bar_h = g.min_h + (g.max_h - g.min_h) * h
            groups.setdefault((round(op * 20) / 20, c), []).append((x0 + i * (g.bar_w + g.gap), cy - bar_h / 2, bar_h))
        for (op, c), bars in groups.items():
            if op <= 0:
                continue
            builder = Gsk.PathBuilder.new()
            for x, y, bar_h in bars:
                builder.add_rounded_rect(_rounded(x, y, g.bar_w, bar_h, radius))
            snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING, self._rgba(c, op))
        if frame.progress is not None:
            self._draw_progress(snapshot, frame.progress, color, x0, cy, height)

    def _draw_progress(self, snapshot, progress: float, color: str, x0: float, cy: float, height: float) -> None:
        g = self._geo
        thick = 2.0 if self._geo_name in ("hero", "row", "pill") else 1.0
        gap = max(1.0, (height / 2 - g.max_h / 2 - thick) / 2)
        y = min(height - thick, cy + g.max_h / 2 + gap)
        track = Gsk.PathBuilder.new()
        track.add_rounded_rect(_rounded(x0, y, g.width, thick, thick / 2))
        snapshot.append_fill(track.to_path(), Gsk.FillRule.WINDING, self._rgba(color, 0.16))
        w = g.width * progress
        if w >= thick:
            # shrinks toward the centre, like the window closing from both ends
            fill = Gsk.PathBuilder.new()
            fill.add_rounded_rect(_rounded(x0 + (g.width - w) / 2, y, w, thick, thick / 2))
            snapshot.append_fill(fill.to_path(), Gsk.FillRule.WINDING, self._rgba(color, 0.9))


def _shown(state: str, detail: str) -> str:
    """The big model at work gets its own heavier animation."""
    return "big" if state == "thinking" and is_big(detail) else state


def _rounded(x: float, y: float, w: float, h: float, r: float) -> Gsk.RoundedRect:
    # init in place: PyGObject returns a dangling copy from the chained form
    rect = Graphene.Rect()
    rect.init(x, y, w, h)
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(rect, min(r, w / 2, h / 2))
    return rounded


__all__ = ["WaveBars", "FOLLOWUP_WINDOW_S"]
