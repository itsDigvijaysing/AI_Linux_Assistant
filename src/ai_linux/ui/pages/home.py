"""Home: one status row (bars, state, listening controls), today's conversation and a text composer."""

from __future__ import annotations

import datetime as dt
import logging
import threading

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ai_linux.engine.timers import spoken_span
from ai_linux.paths import APP_NAME
from ai_linux.schema import BIG_WAKING, SPEEDS
from ai_linux.state import ACTIVE_STATES, STATE_LABELS, AssistantState, Turn
from ai_linux.ui import bar_model, caption_model
from ai_linux.ui.widgets.conversation import (
    LATENCY_PARTS,
    ROUTE_HARDWARE,
    ROUTE_LABELS,
    TurnView,
    format_seconds,
    latency_text,
    total_latency,
)

log = logging.getLogger(__name__)

MODES = (("wake", "Wake word"), ("hotkey", "Hotkey"), ("always", "Always on"))  # Stop is its own button
EXAMPLES = ("What time is it?", "How much battery is left?", "Open my Downloads folder")  # harmless first tries
NO_WAKE_HINT_S = 4  # how long "say the wake word first" stays
STATE_CLASSES = tuple(s.value for s in AssistantState)


def timer_text(item: dict) -> tuple[str, str]:
    """(title, subtitle) for a pending timer: when it rings, not a countdown, so nothing ticks while idle."""
    message = str(item.get("message") or "").strip()
    title = message[:1].upper() + message[1:] if message else "Timer"
    when = dt.datetime.fromtimestamp(float(item.get("due") or 0)).strftime("%-I:%M %p")
    length = int(item.get("length") or 0)
    return title, f"Rings at {when}" + (f", set for {spoken_span(length)}" if length else "")


def hotkey_label(accel: str) -> str:
    """'<Super><Alt>space' -> 'Alt+Super+Space', in the desktop's own wording."""
    try:
        ok, key, mods = Gtk.accelerator_parse(accel or "")
        if ok and key:
            return Gtk.accelerator_get_label(key, mods)
    except Exception:
        log.debug("unparsable hotkey %r", accel, exc_info=True)
    return accel or "the hotkey"


class _StaticBars(Gtk.DrawingArea):
    """Stand-in for ui/bars.WaveBars while it is missing: the resting taper in the state colour."""

    def __init__(self, state: str):
        g = bar_model.GEOMETRY["row"]
        super().__init__(content_width=int(g.width), content_height=int(g.max_h), valign=Gtk.Align.CENTER)
        self._color = bar_model.COLORS["off"]
        self.set_state(state)
        self.set_draw_func(self._draw)

    def set_state(self, state: str) -> None:
        self._color = bar_model.COLORS.get(state, bar_model.COLORS["off"])
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        g = bar_model.GEOMETRY["row"]
        x0 = (width - g.width) / 2
        cr.set_source_rgb(*bar_model.hex_to_rgb(self._color))
        r = g.bar_w / 2
        for i, taper in enumerate(bar_model.TAPER):
            h = g.min_h + (g.max_h - g.min_h) * 0.32 * taper
            x, y = x0 + i * (g.bar_w + g.gap), (height - h) / 2
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, 3.14159, 0)
            cr.arc(x + r, y + h - r, r, 0, 3.14159)
            cr.close_path()
        cr.fill()


class _EmptyState(Gtk.Box):
    """A small icon and two lines: the conversation area can be short, and a StatusPage's icon clipped there."""

    def __init__(self, icon: str, title: str, examples=(), on_example=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                         halign=Gtk.Align.CENTER, vexpand=True)
        self.add_css_class("ail-home-empty")
        image = Gtk.Image(icon_name=icon, pixel_size=40)
        image.add_css_class("dim-label")
        image.set_margin_bottom(6)
        self.append(image)
        heading = Gtk.Label(label=title, justify=Gtk.Justification.CENTER, wrap=True)
        heading.add_css_class("title-4")
        self.append(heading)
        self._desc = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True, use_markup=True)
        self._desc.add_css_class("dim-label")
        self.append(self._desc)
        self._examples = Adw.WrapBox(child_spacing=8, line_spacing=8, align=0.5, halign=Gtk.Align.CENTER)
        self._examples.set_margin_top(14)
        for text in examples:
            button = Gtk.Button(label=text, css_classes=["ail-example"])
            button.connect("clicked", lambda _b, t=text: on_example and on_example(t))
            self._examples.append(button)
        self.append(self._examples)

    def set_examples_sensitive(self, on: bool) -> None:
        self._examples.set_sensitive(on)

    def set_description(self, markup: str) -> None:
        self._desc.set_markup(markup)


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Home", tag="home")
        self.ctx = ctx
        self._views: dict[str, TurnView] = {}
        self._turns: dict[str, Turn] = {}
        self._handlers: list[tuple[object, int]] = []
        self._timers: list[int] = []
        self._stick = True
        self._syncing_mode = False
        self._day = dt.date.today()
        self._static_bars: _StaticBars | None = None
        self._hint_timer = 0

        actions = Gio.SimpleActionGroup()
        self._delete_action = Gio.SimpleAction.new("delete-session", None)
        self._delete_action.connect("activate", lambda *_: self._delete_session())
        actions.add_action(self._delete_action)
        self.insert_action_group("home", actions)
        self._progress_timer = 0

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(self._build_body())
        toolbar.add_bottom_bar(self._build_bottom())
        self.set_child(toolbar)

        bus, settings = ctx.bus, ctx.settings
        self._connect(bus, "state-changed", lambda _b, s, d: self._on_state(s, d))
        self._connect(bus, "turn-added", lambda _b, t: self._on_turn_added(t))
        self._connect(bus, "live-text", lambda _b, text, stable, final: self._on_live(text, stable, final))
        self._connect(settings, "changed::live_text", lambda *_: settings.get("live_text") or self._hide_live())
        self._connect(bus, "turn-updated", lambda _b, t: self._upsert(t))
        self._connect(bus, "session-cleared", lambda *_: self._on_session_cleared())
        self._connect(bus, "wake-missed", lambda *_: self._on_wake_missed())
        self._connect(bus, "timers-changed", lambda *_: self._sync_timers())
        self._connect(settings, "changed::speed", lambda *_: self._sync_speed())
        self._connect(settings, "profile-changed", lambda *_: self._sync_speed())
        for key in ("listen_mode", "muted", "wake_word", "hotkey"):
            self._connect(settings, f"changed::{key}", lambda *_: self._on_listen_settings())
        # the app's own light/dark, which a CSS media query doesn't follow when the theme is forced
        style = Adw.StyleManager.get_default()
        self._connect(style, "notify::dark", lambda *_: self._on_dark(style))
        self._on_dark(style)
        if ctx.history is not None and hasattr(ctx.history, "add_listener"):
            ctx.history.add_listener(self._on_history_event)
        self._timers.append(GLib.timeout_add_seconds(60, self._check_day))
        self.connect("shown", lambda *_: self._entry.grab_focus())
        self.connect("destroy", self._on_destroy)

        self._load_today()
        self._sync_timers()
        self._on_listen_settings()
        self._on_state(bus.state.value, bus.detail)
        live = getattr(bus, "live", None)
        if live and live[0]:
            self._on_live(*live)  # this page was made mid-sentence

    # construction

    def _build_header(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Delete This Session", "home.delete-session")
        new = Adw.SplitButton(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="New Session"),
                              menu_model=menu, dropdown_tooltip="More Session Options",
                              tooltip_text="Start a fresh conversation. History keeps everything.")
        new.connect("clicked", lambda *_: self.ctx.controller.new_session())
        header.pack_start(new)

        # the speed of the column in use (Models has both): one row, Save Battery to Max Performance
        self._speed = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for name, label, what in SPEEDS:
            self._speed.add(Adw.Toggle(name=name, label=label, tooltip=what))
        self._speed.connect("notify::active-name", self._on_speed_picked)
        header.set_title_widget(self._speed)
        self._sync_speed()
        return header

    def _sync_speed(self) -> None:
        speed = str(self.ctx.settings.get("speed"))
        where = "on the charger" if self.ctx.settings.active_profile() == "charger" else "on battery"
        self._speed.set_tooltip_text(f"Speed {where}. Models sets it for battery and charger separately.")
        if self._speed.get_active_name() != speed:
            self._speed.set_active_name(speed)

    def _on_speed_picked(self, group: Adw.ToggleGroup, _pspec) -> None:
        name = group.get_active_name()
        if name and name != self.ctx.settings.get("speed"):
            self.ctx.settings.set("speed", name)

    def _delete_session(self) -> None:
        """The session leaves Home, History and the model's memory; Undo brings the turns back."""
        turns = sorted(self._turns.values(), key=lambda t: t.ts)
        if not turns or self.ctx.history is None:
            return
        start = self.ctx.bus.session_start
        try:
            self.ctx.controller.delete_session(self.ctx.history, turns)
        except Exception:
            log.exception("deleting the session")
            return
        n = len(turns)
        toast = Adw.Toast(title=f"Deleted {n} request{'s' if n != 1 else ''}", button_label="Undo", timeout=6)
        toast.connect("button-clicked", lambda *_: self._undo_delete(turns, start))
        root = self.get_root()
        if hasattr(root, "add_toast"):
            root.add_toast(toast)

    def _undo_delete(self, turns: list[Turn], start: float) -> None:
        try:
            self.ctx.controller.restore_session(self.ctx.history, turns, start)
        except Exception:
            log.exception("restoring the session")

    def _on_session_cleared(self) -> None:
        self._hide_live(now=True)
        self._clear_views()
        self._load_today()

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self._build_hero())
        body.append(self._build_notice())
        self._timer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self._timer_clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=self._timer_list,
                                      margin_start=16, margin_end=16, margin_top=12, visible=False)
        body.append(self._timer_clamp)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self._list.set_margin_top(18)
        self._list.set_margin_bottom(24)
        self._list.set_margin_start(16)
        self._list.set_margin_end(16)
        self._section = Gtk.Label(label="TODAY", xalign=0)
        self._section.add_css_class("ail-section-label")
        self._list.append(self._section)
        self._list.append(self._build_live())

        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=self._list)
        self._scroller = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        adj = self._scroller.get_vadjustment()
        adj.connect("value-changed", self._on_scrolled)
        adj.connect("changed", self._on_adj_changed)

        self._empty = _EmptyState("chat-message-new-symbolic", "Nothing yet today", EXAMPLES, self._send_text)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(self._scroller, "list")
        self._stack.add_css_class("ail-conversation-scroller")
        body.append(self._stack)
        return body

    def _build_live(self) -> Gtk.Widget:
        """Your words while you speak, as the user bubble the turn will have; always the list's last row."""
        self._live_label = Gtk.Label(wrap=True, xalign=0, use_markup=True, max_width_chars=52)
        bubble = Gtk.Box(halign=Gtk.Align.END, css_classes=["ail-bubble", "user", "live"])
        bubble.append(self._live_label)
        self._live = Gtk.Revealer(child=bubble, halign=Gtk.Align.END, visible=False,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=160)
        self._live.connect("notify::child-revealed",
                           lambda r, _p: r.get_reveal_child() or r.get_child_revealed() or self._live_gone())
        return self._live

    def _build_hero(self) -> Gtk.Widget:
        """One row: what it is doing on the left, how it listens on the right; wraps on a narrow window."""
        status = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        status.append(self._make_bars())
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self._state_lbl = Gtk.Label(xalign=0)
        self._state_lbl.add_css_class("ail-state-label")
        text.append(self._state_lbl)
        self._hint = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=38,
                               width_chars=18)
        self._hint.add_css_class("dimmed")
        self._hint.add_css_class("ail-hero-hint")
        text.append(self._hint)
        status.append(text)

        self._modes = Gtk.DropDown.new_from_strings([label for _name, label in MODES])
        self._modes.set_valign(Gtk.Align.CENTER)
        self._modes.update_property([Gtk.AccessibleProperty.LABEL], ["How to listen"])
        self._modes.connect("notify::selected", self._on_mode_picked)

        self._run_content = Adw.ButtonContent(icon_name="media-playback-stop-symbolic", label="Stop")
        self._run = Gtk.Button(child=self._run_content, valign=Gtk.Align.CENTER, css_classes=["pill", "ail-listen-btn"])
        self._run.connect("clicked", self._on_run_clicked)

        controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        controls.append(self._modes)
        controls.append(self._run)

        row = Adw.WrapBox(child_spacing=16, line_spacing=10, justify=Adw.JustifyMode.SPREAD, justify_last_line=True)
        row.append(status)
        row.append(controls)
        self._progress = Gtk.ProgressBar(pulse_step=0.08, visible=False, css_classes=["ail-hero-progress"])
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.append(row)
        column.append(self._progress)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=column)
        clamp.add_css_class("ail-hero")
        clamp.set_margin_start(16)
        clamp.set_margin_end(16)
        return clamp

    def _make_bars(self) -> Gtk.Widget:
        try:
            from ai_linux.ui.bars import WaveBars
        except ImportError as exc:
            log.info("animated bars not available yet (%s); using static bars", exc)
        else:
            try:
                bars = WaveBars(self.ctx.bus, "row")
                bars.set_valign(Gtk.Align.CENTER)
                return bars
            except Exception:
                log.exception("WaveBars failed; using static bars")
        self._static_bars = _StaticBars(self.ctx.bus.state.value)
        return self._static_bars

    def _build_notice(self) -> Gtk.Widget:
        card = Gtk.Box(spacing=14)
        card.add_css_class("ail-notice")
        icon = Gtk.Image(icon_name="dialog-information-symbolic", pixel_size=20, valign=Gtk.Align.START)
        icon.add_css_class("ail-notice-icon")
        card.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        title = Gtk.Label(label="The speech engine isn't installed", xalign=0, wrap=True)
        title.add_css_class("ail-notice-title")
        text.append(title)
        text.append(Gtk.Label(label=f"{APP_NAME} can't listen or speak until it is. Health shows what's missing.",
                              xalign=0, wrap=True))
        card.append(text)
        health = Gtk.Button(label="Open Health", valign=Gtk.Align.CENTER, css_classes=["pill"])
        health.connect("clicked", lambda *_: self._show_page("health"))
        card.append(health)

        clamp = Adw.Clamp(maximum_size=600, child=card)
        clamp.set_margin_top(14)
        clamp.set_margin_bottom(14)
        clamp.set_margin_start(16)
        clamp.set_margin_end(16)
        self._notice = Gtk.Revealer(child=clamp, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        return self._notice

    def _build_bottom(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("ail-bottom")

        composer = Gtk.Box(spacing=8)
        self._entry = Gtk.Entry(hexpand=True, placeholder_text="Type a request and press Enter")
        self._entry.add_css_class("ail-composer-entry")
        self._entry.update_property([Gtk.AccessibleProperty.LABEL], ["Request"])
        self._entry.connect("activate", lambda *_: self._submit())
        self._entry.connect("changed", lambda e: self._send.set_sensitive(bool(e.get_text().strip())))
        composer.append(self._entry)
        self._send = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Send", sensitive=False,
                                valign=Gtk.Align.CENTER)
        for c in ("circular", "suggested-action", "ail-send-btn"):
            self._send.add_css_class(c)
        self._send.update_property([Gtk.AccessibleProperty.LABEL], ["Send"])
        self._send.connect("clicked", lambda *_: self._submit())
        composer.append(self._send)

        self._stats = Adw.WrapBox(child_spacing=18, line_spacing=6, align=0.5)
        self._stats.add_css_class("ail-stats")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.append(composer)
        inner.append(self._stats)
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=inner)
        clamp.set_margin_start(16)
        clamp.set_margin_end(16)
        box.append(clamp)
        return box

    # helpers

    def _connect(self, obj, signal: str, cb) -> None:
        self._handlers.append((obj, obj.connect(signal, cb)))

    def _sync_timers(self) -> None:
        """Pending timers and reminders, with Cancel; the engine keeps them in timers.json."""
        self._timer_list.remove_all()
        items = list(getattr(self.ctx.bus, "timers", []) or [])
        for item in items:
            title, subtitle = timer_text(item)
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="alarm-symbolic"))
            cancel = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER, css_classes=["flat"])
            cancel.connect("clicked", lambda *_, tid=str(item.get("id")): self.ctx.controller.cancel_timer(tid))
            row.add_suffix(cancel)
            self._timer_list.append(row)
        self._timer_clamp.set_visible(bool(items))

    def _on_destroy(self, *_):
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        for t in self._timers:
            GLib.source_remove(t)
        self._timers.clear()
        if self._hint_timer:
            GLib.source_remove(self._hint_timer)
            self._hint_timer = 0
        self._show_progress(False)
        if self.ctx.history is not None and hasattr(self.ctx.history, "remove_listener"):
            self.ctx.history.remove_listener(self._on_history_event)

    # state and listening mode

    def _on_dark(self, style) -> None:
        (self._state_lbl.add_css_class if style.get_dark() else self._state_lbl.remove_css_class)("ail-dark")

    def _on_state(self, state: str, detail: str) -> None:
        try:
            st = AssistantState(state)
        except ValueError:
            st = AssistantState.OFF
        self._state_lbl.set_label(STATE_LABELS.get(st, state.capitalize()))
        for c in STATE_CLASSES:
            self._state_lbl.remove_css_class(c)
        self._state_lbl.add_css_class(st.value)
        if self._static_bars is not None:
            self._static_bars.set_state(st.value)

        engine = bool(getattr(self.ctx.controller, "engine_available", False))
        off_notice = not engine and st == AssistantState.OFF
        self._notice.set_reveal_child(off_notice)
        hint = "" if off_notice else (detail or self._hint_for(st))
        self._hint.set_label(hint)
        self._hint.set_visible(bool(hint))  # the bars set the row's height, so nothing jumps
        self._hint.set_tooltip_text(hint or None)  # a long detail is cut to one line
        self._empty.set_examples_sensitive(engine)

        self._run.set_sensitive(engine)
        self._sync_run()
        busy = st == AssistantState.LOADING or (st == AssistantState.THINKING and detail == BIG_WAKING)
        self._show_progress(busy)

    def _sync_run(self) -> None:
        """Start when listening is stopped (the microphone is off), Stop while it listens."""
        stopped = bool(self.ctx.settings.get("muted")) or self.ctx.settings.get("listen_mode") == "off"
        self._run_content.set_label("Start" if stopped else "Stop")
        self._run_content.set_icon_name("media-playback-start-symbolic" if stopped else "media-playback-stop-symbolic")
        (self._run.add_css_class if stopped else self._run.remove_css_class)("suggested-action")
        engine = bool(getattr(self.ctx.controller, "engine_available", False))
        self._run.set_tooltip_text("Available once the speech engine is installed" if not engine else
                                   "Start listening" if stopped else "Stop listening and turn the microphone off")

    def _show_progress(self, on: bool) -> None:
        """A moving bar while models load, so a wait never looks like nothing is happening."""
        self._progress.set_visible(on)
        if on and not self._progress_timer:
            self._progress_timer = GLib.timeout_add(120, self._pulse)
        elif not on and self._progress_timer:
            GLib.source_remove(self._progress_timer)
            self._progress_timer = 0

    def _pulse(self) -> bool:
        self._progress.pulse()
        return GLib.SOURCE_CONTINUE

    def _hint_for(self, st: AssistantState) -> str:
        mode = self.ctx.settings.get("listen_mode")
        if st == AssistantState.IDLE:
            return {
                "wake": f"Say “{self.ctx.settings.get('wake_word')}” to start",
                "hotkey": f"Press {hotkey_label(self.ctx.settings.get('hotkey'))} to talk",
                "always": "Listening for requests",
            }.get(mode, "")
        return {
            AssistantState.FOLLOWUP: "Go ahead, no wake word needed",
            AssistantState.MUTED: "Press Start to listen again",
            AssistantState.LOADING: "Getting ready",
            AssistantState.OFF: "The assistant is not running",
            AssistantState.ERROR: "Something went wrong",
        }.get(st, "")

    def _on_listen_settings(self) -> None:
        s = self.ctx.settings
        mode = s.get("listen_mode")
        muted = bool(s.get("muted")) or mode == "off"
        names = [name for name, _label in MODES]
        self._syncing_mode = True
        try:
            if mode in names and self._modes.get_selected() != names.index(mode):
                self._modes.set_selected(names.index(mode))  # "off" keeps the last way shown; Start resumes it
        finally:
            self._syncing_mode = False
        wake, keys = s.get("wake_word"), hotkey_label(s.get("hotkey"))
        self._modes.set_tooltip_text({
            "wake": f"Listens for “{wake}”",
            "hotkey": f"Listens after you press {keys}",
            "always": "Listens all the time, no wake word",
        }.get(mode, "How to listen once the microphone is on"))
        if muted:
            mode = "off"
        self._empty.set_description(GLib.markup_escape_text({
            "wake": f"Say “{wake}” or press {keys}",
            "hotkey": f"Press {keys} or type below",
            "always": "Just start talking, or type below",
            "off": "Listening is stopped. Press Start, or type below",
        }.get(mode, "Type a request below")))
        self._on_state(self.ctx.bus.state.value, self.ctx.bus.detail)

    def _on_mode_picked(self, dropdown: Gtk.DropDown, _pspec) -> None:
        i = dropdown.get_selected()
        if self._syncing_mode or i >= len(MODES):
            return
        name = MODES[i][0]
        if name == self.ctx.settings.get("listen_mode") and not self.ctx.settings.get("muted"):
            return
        try:
            self.ctx.controller.set_listen_mode(name)  # picking a way to listen also unmutes
        except Exception:
            log.exception("set_listen_mode(%s)", name)
            self._on_listen_settings()

    def _on_run_clicked(self, *_):
        stopped = bool(self.ctx.settings.get("muted")) or self.ctx.settings.get("listen_mode") == "off"
        try:
            if not stopped and self.ctx.bus.state in ACTIVE_STATES:
                self.ctx.controller.cancel()  # Stop also ends what it is doing
            self.ctx.controller.mute(not stopped)
        except Exception:
            log.exception("start/stop listening")
        self._on_listen_settings()

    def _on_wake_missed(self) -> None:
        """Speech without the wake word was dropped: say so for a moment, on Home only."""
        if self.ctx.bus.state != AssistantState.IDLE:
            return
        self._hint.set_label(f"Heard you. Say “{self.ctx.settings.get('wake_word')}” first")
        self._hint.set_visible(True)
        if self._hint_timer:
            GLib.source_remove(self._hint_timer)
        self._hint_timer = GLib.timeout_add_seconds(NO_WAKE_HINT_S, self._end_wake_hint)

    def _end_wake_hint(self) -> bool:
        self._hint_timer = 0
        self._on_state(self.ctx.bus.state.value, self.ctx.bus.detail)
        return GLib.SOURCE_REMOVE

    def _show_page(self, name: str) -> None:
        root = self.get_root()
        if hasattr(root, "show_page"):
            root.show_page(name)

    def _submit(self) -> None:
        if self._send_text(self._entry.get_text()):
            self._entry.set_text("")

    def _send_text(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        try:
            self.ctx.controller.submit_text(text)
        except Exception:
            log.exception("submit_text")
            return False
        return True

    # conversation

    def _on_turn_added(self, turn: Turn) -> None:
        self._hide_live(now=True)  # the turn's own bubble takes its place
        self._upsert(turn, scroll=True)

    def _on_live(self, text: str, stable: int, final: bool) -> None:
        if not text.strip() or not self.ctx.settings.get("live_text"):
            self._hide_live()
            return
        self._live_label.set_markup(caption_model.live_markup(text, stable, dim=0.6))
        last = self._list.get_last_child()
        if last is not self._live:
            self._list.reorder_child_after(self._live, last)
        if not self._live.get_visible():
            self._live.set_visible(True)
            self._stick = True
            self._refresh()
        self._live.set_reveal_child(True)

    def _hide_live(self, now: bool = False) -> None:
        if not self._live.get_visible():
            return
        self._live.set_reveal_child(False)
        if now or not self._live.get_mapped():
            self._live_gone()

    def _live_gone(self) -> None:
        self._live.set_visible(False)
        self._refresh()

    def _load_today(self) -> None:
        turns: list[Turn] = []
        if self.ctx.history is not None:
            try:
                turns = self.ctx.history.for_day(self._day)
            except Exception:
                log.exception("loading today's history")
        for t in turns:
            self._upsert(t, refresh=False)
        self._stick = True
        self._refresh()

    def _clear_views(self) -> None:
        for view in self._views.values():
            self._list.remove(view)
        self._views.clear()
        self._turns.clear()

    def _upsert(self, turn: Turn, scroll: bool = False, refresh: bool = True) -> None:
        try:
            self._check_day()
            if dt.date.fromtimestamp(turn.ts) != self._day or turn.ts < self.ctx.bus.session_start:
                return
            view = self._views.get(turn.id)
            if view is not None:
                view.update(turn)
            else:
                view = TurnView(turn)
                self._list.append(view)
                self._views[turn.id] = view
            self._turns[turn.id] = turn
            if scroll:
                self._stick = True
            if refresh:
                self._refresh()
        except Exception:
            log.exception("showing turn")

    def _remove(self, turn_id: str) -> None:
        view = self._views.pop(turn_id, None)
        self._turns.pop(turn_id, None)
        if view is not None:
            self._list.remove(view)
            self._refresh()

    def _check_day(self) -> bool:
        today = dt.date.today()
        if today != self._day:
            self._day = today
            self._clear_views()
            self._refresh()
        return True

    def _on_history_event(self, event: str, turn_id: str | None) -> None:
        # The store calls back on the writer's thread.
        if threading.current_thread() is not threading.main_thread():
            GLib.idle_add(lambda: self._on_history_event(event, turn_id) and False)
            return
        if event == "delete" and turn_id:
            self._remove(turn_id)
        elif event in ("clear", "prune"):
            self._clear_views()
            self._load_today()

    def _refresh(self) -> None:
        has = bool(self._views)
        self._stack.set_visible_child_name("list" if has or self._live.get_visible() else "empty")
        n = len(self._views)
        self._section.set_label(f"TODAY · {n} REQUEST{'S' if n != 1 else ''}")
        self._delete_action.set_enabled(has and self.ctx.history is not None)
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        box = self._stats
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

        n = len(self._turns)
        box.set_visible(bool(n))  # nothing to count yet: the empty state says enough
        if not n:
            return
        count = Gtk.Box(spacing=4)
        count.append(_stat_label(str(n), "ail-stat-value", "numeric"))
        count.append(_stat_label("request today" if n == 1 else "requests today", "dimmed"))
        box.append(count)

        last = max(self._turns.values(), key=lambda t: t.ts)
        lat = {k: v for k, v in (last.latency or {}).items() if isinstance(v, (int, float))}
        if lat:
            group = Gtk.Box(spacing=10, tooltip_text=latency_text(last))
            total = total_latency(last) or 0.0
            head = Gtk.Box(spacing=6)
            head.append(_stat_label("Last reply", "dimmed"))
            head.append(_stat_label(format_seconds(total), "ail-stat-value", "numeric"))
            group.append(head)
            group.append(_latency_bar(lat, total))
            for key, label in LATENCY_PARTS:
                if key in lat:
                    part = Gtk.Box(spacing=5)
                    dot = Gtk.Box(valign=Gtk.Align.CENTER, width_request=8, height_request=8)
                    for c in ("ail-lat-seg", key, "ail-dot"):
                        dot.add_css_class(c)
                    part.append(dot)
                    part.append(_stat_label(label, "dimmed"))
                    part.append(_stat_label(format_seconds(lat[key]), "numeric"))
                    group.append(part)
            box.append(group)

        if last.route in ROUTE_LABELS:
            who = Gtk.Box(spacing=6)
            chip = _stat_label(ROUTE_LABELS[last.route], "ail-chip", f"route-{last.route}")
            chip.set_valign(Gtk.Align.CENTER)
            who.append(chip)
            hw = ROUTE_HARDWARE[last.route]
            who.append(_stat_label(f"{last.model} on {hw}" if last.model else f"on {hw}", "dimmed"))
            box.append(who)

    def _on_scrolled(self, adj: Gtk.Adjustment) -> None:
        self._stick = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 32

    def _on_adj_changed(self, adj: Gtk.Adjustment) -> None:
        if self._stick:
            adj.set_value(adj.get_upper() - adj.get_page_size())


def _stat_label(text: str, *classes: str) -> Gtk.Label:
    lbl = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def _latency_bar(lat: dict[str, float], total: float, width: int = 96) -> Gtk.Widget:
    """Stacked bar of the four stages, so the slow one stands out at a glance."""
    bar = Gtk.Box(valign=Gtk.Align.CENTER, width_request=width)
    bar.add_css_class("ail-lat-bar")
    keys = [k for k, _ in LATENCY_PARTS if lat.get(k, 0) > 0]
    remaining = width
    for i, key in enumerate(keys):
        if i == len(keys) - 1:
            w = remaining  # last segment absorbs rounding
        else:
            w = max(3, round(width * lat[key] / total)) if total else 0
        w = max(0, min(w, remaining))
        remaining -= w
        seg = Gtk.Box(width_request=w)
        seg.add_css_class("ail-lat-seg")
        seg.add_css_class(key)
        bar.append(seg)
    return bar
