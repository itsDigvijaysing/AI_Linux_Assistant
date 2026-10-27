"""Home: live assistant state, listening mode, today's conversation and a text composer."""

from __future__ import annotations

import datetime as dt
import logging
import threading

from gi.repository import Adw, GLib, Gtk

from ai_linux.state import ACTIVE_STATES, STATE_LABELS, AssistantState, Turn
from ai_linux.ui import bar_model
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

MODES = (("wake", "Wake word"), ("hotkey", "Hotkey"), ("always", "Always on"), ("off", "Mute"))
STATE_CLASSES = tuple(s.value for s in AssistantState)


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
        g = bar_model.GEOMETRY["hero"]
        super().__init__(content_width=int(g.width), content_height=int(g.max_h), halign=Gtk.Align.CENTER)
        self._color = bar_model.COLORS["off"]
        self.set_state(state)
        self.set_draw_func(self._draw)

    def set_state(self, state: str) -> None:
        self._color = bar_model.COLORS.get(state, bar_model.COLORS["off"])
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        g = bar_model.GEOMETRY["hero"]
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

    def __init__(self, icon: str, title: str):
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

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self._build_body())
        toolbar.add_bottom_bar(self._build_bottom())
        self.set_child(toolbar)

        bus, settings = ctx.bus, ctx.settings
        self._connect(bus, "state-changed", lambda _b, s, d: self._on_state(s, d))
        self._connect(bus, "turn-added", lambda _b, t: self._upsert(t, scroll=True))
        self._connect(bus, "turn-updated", lambda _b, t: self._upsert(t))
        for key in ("listen_mode", "wake_word", "hotkey"):
            self._connect(settings, f"changed::{key}", lambda *_: self._on_listen_settings())
        if ctx.history is not None and hasattr(ctx.history, "add_listener"):
            ctx.history.add_listener(self._on_history_event)
        self._timers.append(GLib.timeout_add_seconds(60, self._check_day))
        self.connect("shown", lambda *_: self._entry.grab_focus())
        self.connect("destroy", self._on_destroy)

        self._load_today()
        self._on_listen_settings()
        self._on_state(bus.state.value, bus.detail)

    # construction

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self._build_hero())
        body.append(self._build_notice())

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self._list.set_margin_top(18)
        self._list.set_margin_bottom(24)
        self._list.set_margin_start(16)
        self._list.set_margin_end(16)
        self._section = Gtk.Label(label="TODAY", xalign=0)
        self._section.add_css_class("ail-section-label")
        self._list.append(self._section)

        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=self._list)
        self._scroller = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        adj = self._scroller.get_vadjustment()
        adj.connect("value-changed", self._on_scrolled)
        adj.connect("changed", self._on_adj_changed)

        self._empty = _EmptyState("chat-message-new-symbolic", "Nothing yet today")

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(self._scroller, "list")
        self._stack.add_css_class("ail-conversation-scroller")
        body.append(self._stack)
        return body

    def _build_hero(self) -> Gtk.Widget:
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        hero.add_css_class("ail-hero")
        hero.set_margin_start(16)
        hero.set_margin_end(16)
        hero.append(self._make_bars())

        self._state_lbl = Gtk.Label(justify=Gtk.Justification.CENTER)
        self._state_lbl.add_css_class("ail-state-label")
        hero.append(self._state_lbl)
        self._hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=48)
        self._hint.add_css_class("dimmed")
        self._hint.add_css_class("ail-hero-hint")
        hero.append(self._hint)

        self._modes = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        self._modes.add_css_class("round")
        self._mode_toggles: dict[str, Adw.Toggle] = {}
        for name, label in MODES:
            toggle = Adw.Toggle(name=name, label=label)
            self._modes.add(toggle)
            self._mode_toggles[name] = toggle
        self._modes.connect("notify::active-name", self._on_mode_picked)

        self._listen_content = Adw.ButtonContent(icon_name="audio-input-microphone-symbolic", label="Listen now")
        self._listen = Gtk.Button(child=self._listen_content, valign=Gtk.Align.CENTER)
        for c in ("pill", "suggested-action", "ail-listen-btn"):
            self._listen.add_css_class(c)
        self._listen.connect("clicked", self._on_listen_clicked)

        controls = Adw.WrapBox(child_spacing=12, line_spacing=10, align=0.5, halign=Gtk.Align.CENTER)
        controls.set_margin_top(18)
        controls.append(self._modes)
        controls.append(self._listen)
        hero.append(controls)
        return hero

    def _make_bars(self) -> Gtk.Widget:
        try:
            from ai_linux.ui.bars import WaveBars
        except ImportError as exc:
            log.info("animated bars not available yet (%s); using static bars", exc)
        else:
            try:
                bars = WaveBars(self.ctx.bus, "hero")
                bars.set_halign(Gtk.Align.CENTER)
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
        title = Gtk.Label(label="The speech engine arrives in the next build", xalign=0, wrap=True)
        title.add_css_class("ail-notice-title")
        text.append(title)
        body = Gtk.Label(
            label="Listening and spoken replies are not wired in yet. To see how it will work, "
                  "start the app with --demo for a simulation.",
            xalign=0, wrap=True,
        )
        text.append(body)
        code = Gtk.Label(label="bin/ai-linux-app --demo", xalign=0, halign=Gtk.Align.START, selectable=True)
        for c in ("monospace", "ail-code"):
            code.add_css_class(c)
        text.append(code)
        card.append(text)

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
        if self.ctx.history is not None and hasattr(self.ctx.history, "remove_listener"):
            self.ctx.history.remove_listener(self._on_history_event)

    # state and listening mode

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
        self._hint.set_label(hint or "\u00a0")  # keep the line so the controls don't jump between states

        active = st in ACTIVE_STATES
        self._listen_content.set_label("Stop" if active else "Listen now")
        self._listen_content.set_icon_name("media-playback-stop-symbolic" if active else
                                           "audio-input-microphone-symbolic")
        self._listen.set_sensitive(engine)
        self._listen.set_tooltip_text(None if engine else "Available once the speech engine is installed")

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
            AssistantState.MUTED: "The microphone is off",
            AssistantState.LOADING: "Warming up the models",
            AssistantState.OFF: "The assistant is not running",
            AssistantState.ERROR: "Something went wrong",
        }.get(st, "")

    def _on_listen_settings(self) -> None:
        s = self.ctx.settings
        mode = s.get("listen_mode")
        self._syncing_mode = True
        try:
            if self._modes.get_active_name() != mode:
                self._modes.set_active_name(mode)
        finally:
            self._syncing_mode = False
        wake, keys = s.get("wake_word"), hotkey_label(s.get("hotkey"))
        self._mode_toggles["wake"].set_tooltip(f"Listen for “{wake}”")
        self._mode_toggles["hotkey"].set_tooltip(f"Talk after pressing {keys}")
        self._mode_toggles["always"].set_tooltip("Listen all the time, no wake word")
        self._mode_toggles["off"].set_tooltip("Turn the microphone off")
        self._empty.set_description(GLib.markup_escape_text({
            "wake": f"Say “{wake}” or press {keys}",
            "hotkey": f"Press {keys} or type below",
            "always": "Just start talking, or type below",
            "off": "The microphone is muted. Type a request below",
        }.get(mode, "Type a request below")))
        self._on_state(self.ctx.bus.state.value, self.ctx.bus.detail)

    def _on_mode_picked(self, group: Adw.ToggleGroup, _pspec) -> None:
        name = group.get_active_name()
        if self._syncing_mode or not name or name == self.ctx.settings.get("listen_mode"):
            return
        try:
            self.ctx.controller.set_listen_mode(name)
        except Exception:
            log.exception("set_listen_mode(%s)", name)
            self._on_listen_settings()

    def _on_listen_clicked(self, *_):
        try:
            self.ctx.controller.toggle_listening()
        except Exception:
            log.exception("toggle_listening")

    def _submit(self) -> None:
        text = self._entry.get_text().strip()
        if not text:
            return
        try:
            self.ctx.controller.submit_text(text)
        except Exception:
            log.exception("submit_text")
            return
        self._entry.set_text("")

    # conversation

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
            if dt.date.fromtimestamp(turn.ts) != self._day:
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
        self._stack.set_visible_child_name("list" if has else "empty")
        n = len(self._views)
        self._section.set_label(f"TODAY · {n} TURN{'S' if n != 1 else ''}")
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        box = self._stats
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

        n = len(self._turns)
        count = Gtk.Box(spacing=4)
        if n:
            count.append(_stat_label(str(n), "ail-stat-value", "numeric"))
            count.append(_stat_label("turn today" if n == 1 else "turns today", "dimmed"))
        else:
            count.append(_stat_label("No turns yet today", "dimmed"))
        box.append(count)
        if not n:
            return

        last = max(self._turns.values(), key=lambda t: t.ts)
        lat = {k: v for k, v in (last.latency or {}).items() if isinstance(v, (int, float))}
        if lat:
            group = Gtk.Box(spacing=10, tooltip_text=latency_text(last))
            total = total_latency(last) or 0.0
            head = Gtk.Box(spacing=6)
            head.append(_stat_label("Last turn", "dimmed"))
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
