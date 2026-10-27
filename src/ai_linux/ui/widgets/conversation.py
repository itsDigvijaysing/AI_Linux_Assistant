"""One conversation turn: chat bubbles (Home) or a compact log row (History), tool chips and meta."""

from __future__ import annotations

import functools
import logging
import time

from gi.repository import Gio, GLib, Gtk, Pango

from ai_linux.state import ToolCall, Turn

log = logging.getLogger(__name__)

ROUTE_LABELS = {"small": "Small model", "big": "Big model"}
ROUTE_HARDWARE = {"small": "CPU", "big": "GPU"}
LATENCY_PARTS = (("asr", "Speech"), ("route", "Routing"), ("llm", "Model"), ("tts", "Voice"))
# verdict -> (badge text, css class); classes map to libadwaita's success/warning/error colours
VERDICTS = {
    "ran": ("Ran", "success"),
    "blocked": ("Blocked", "error"),
    "denied": ("Denied", "warning"),
    "needs_confirm": ("Needs confirmation", "warning"),
    "error": ("Error", "error"),
}
STATUS_LABELS = {"failed": "Failed", "cancelled": "Cancelled", "partial": "In progress"}


@functools.lru_cache(maxsize=1)
def _clock_12h() -> bool:
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            return Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format") == "12h"
    except Exception:
        log.debug("clock-format unreadable", exc_info=True)
    return False


def format_time(ts: float) -> str:
    """Time of day in the user's GNOME clock format."""
    t = time.localtime(ts)
    if _clock_12h():
        return time.strftime("%I:%M %p", t).lstrip("0")
    return time.strftime("%H:%M", t)


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.2f} s"
    return f"{seconds:.1f} s"


def total_latency(turn: Turn) -> float | None:
    vals = [v for v in (turn.latency or {}).values() if isinstance(v, (int, float))]
    return sum(vals) if vals else None


def latency_text(turn: Turn, sep: str = "\n") -> str:
    lat = turn.latency or {}
    return sep.join(f"{label} {format_seconds(lat[key])}" for key, label in LATENCY_PARTS if key in lat)


def copy_to_clipboard(widget: Gtk.Widget, text: str) -> bool:
    try:
        widget.get_clipboard().set(text)
        return True
    except Exception:
        log.exception("copy to clipboard failed")
        return False


def turn_copy_text(turn: Turn) -> str:
    return turn.reply or turn.heard


def _label(text: str, *classes: str, wrap: bool = False, selectable: bool = False, xalign: float = 0.0,
           tooltip: str | None = None) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign, selectable=selectable)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # long paths and URLs still break
        lbl.set_natural_wrap_mode(Gtk.NaturalWrapMode.WORD)
    for c in classes:
        lbl.add_css_class(c)
    if tooltip:
        lbl.set_tooltip_text(tooltip)
    return lbl


def _chip(text: str, *classes: str, tooltip: str | None = None) -> Gtk.Label:
    chip = _label(text, "ail-chip", *classes, tooltip=tooltip)
    chip.set_valign(Gtk.Align.CENTER)
    return chip


class ToolChip(Gtk.Box):
    """Tool name, the exact command, a verdict badge and the exit code."""

    def __init__(self, call: ToolCall):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("ail-tool")
        verdict = call.verdict or "ran"
        badge_text, badge_cls = VERDICTS.get(verdict, (verdict.replace("_", " ").capitalize(), "warning"))
        self.add_css_class(f"verdict-{badge_cls}")

        row = Gtk.Box(spacing=8)
        icon = Gtk.Image(icon_name="utilities-terminal-symbolic" if call.command else "system-run-symbolic")
        icon.add_css_class("ail-tool-icon")
        row.append(icon)
        row.append(_label(call.name, "ail-tool-name"))

        detail = call.command or self._args_summary(call.args)
        cmd = _label(detail, "ail-tool-cmd", "monospace", tooltip=detail or None)
        cmd.set_ellipsize(Pango.EllipsizeMode.END)
        cmd.set_hexpand(True)
        cmd.set_width_chars(8)
        cmd.set_selectable(bool(detail))
        row.append(cmd)

        badge = _label(badge_text, "ail-badge", badge_cls)
        badge.set_valign(Gtk.Align.CENTER)
        row.append(badge)
        if call.exit_code is not None:
            row.append(_label(f"exit {call.exit_code}", "ail-tool-exit", "numeric", "dimmed"))
        self.append(row)

        reason = (call.output or "").strip()
        if reason and verdict != "ran":
            # blocked or denied: the reason is short and is the point of showing the chip
            why = _label(reason, "ail-tool-reason", "caption", wrap=True, selectable=True)
            self.append(why)
        elif reason:
            self.set_tooltip_text(reason[:600])

    @staticmethod
    def _args_summary(args: dict) -> str:
        if not args:
            return ""
        return ", ".join(f"{k}={v}" for k, v in args.items())


class TurnView(Gtk.Box):
    """Bubbles: user right on the accent colour, assistant left on a card. Compact: a log row."""

    def __init__(self, turn: Turn, compact: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.compact = compact
        self.turn = turn
        self._copy_btn: Gtk.Button | None = None
        self._copy_reset = 0
        self.add_css_class("ail-turn")
        self.add_css_class("compact" if compact else "bubbles")
        if not compact:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self.add_css_class("hover"))
            motion.connect("leave", lambda *_: self.remove_css_class("hover"))
            self.add_controller(motion)
        self.connect("unrealize", self._on_unrealize)
        self._build()

    def update(self, turn: Turn) -> None:
        self.turn = turn
        child = self.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        self._build()

    def _build(self) -> None:
        t = self.turn
        for cls in ("failed", "cancelled", "partial"):
            self.remove_css_class(cls)
        if t.status in STATUS_LABELS:
            self.add_css_class(t.status)
        try:
            (self._build_compact if self.compact else self._build_bubbles)(t)
        except Exception:  # a malformed turn must never take the page down
            log.exception("rendering turn %s", getattr(t, "id", "?"))
            self.append(_label(getattr(t, "heard", "") or "(unreadable turn)", wrap=True))

    # bubbles (Home)

    def _build_bubbles(self, t: Turn) -> None:
        self.set_spacing(6)
        if t.heard:
            user = Gtk.Box(halign=Gtk.Align.END)
            user.add_css_class("ail-bubble")
            user.add_css_class("user")
            msg = _label(t.heard, wrap=True, selectable=True)
            msg.set_max_width_chars(52)
            user.append(msg)
            row = Gtk.Box(halign=Gtk.Align.END, spacing=8)
            if t.source == "text":
                row.append(self._source_icon("input-keyboard-symbolic", "Typed"))
            row.append(user)
            self.append(row)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.START)
        column.add_css_class("ail-assistant-col")
        if t.reply or t.status == "partial":
            bubble = Gtk.Box(halign=Gtk.Align.START)
            bubble.add_css_class("ail-bubble")
            bubble.add_css_class("assistant")
            if t.reply:
                msg = _label(t.reply, wrap=True, selectable=True)
            else:
                msg = _label("...", "dimmed")
            msg.set_max_width_chars(60)
            bubble.append(msg)
            column.append(bubble)
        if t.tools:
            tools = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for call in t.tools:
                tools.append(ToolChip(call))
            column.append(tools)
        column.append(self._meta(t, with_copy=True))
        self.append(column)

    @staticmethod
    def _source_icon(name: str, tip: str) -> Gtk.Image:
        img = Gtk.Image(icon_name=name, valign=Gtk.Align.CENTER, tooltip_text=tip)
        img.add_css_class("dimmed")
        return img

    # compact (History)

    def _build_compact(self, t: Turn) -> None:
        self.set_spacing(0)
        row = Gtk.Box(spacing=14)
        when = _label(format_time(t.ts), "ail-turn-time", "numeric", "caption", "dimmed")
        when.set_valign(Gtk.Align.START)
        when.set_width_chars(5)
        row.append(when)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        heard_row = Gtk.Box(spacing=6)
        heard_row.append(self._source_icon(
            "input-keyboard-symbolic" if t.source == "text" else "audio-input-microphone-symbolic",
            "Typed" if t.source == "text" else "Spoken"))
        heard = _label(t.heard or "(nothing heard)", "ail-heard", wrap=True, selectable=True)
        heard.set_hexpand(True)
        heard_row.append(heard)
        body.append(heard_row)
        if t.reply:
            body.append(_label(t.reply, "ail-reply", wrap=True, selectable=True))
        for call in t.tools:
            body.append(ToolChip(call))
        body.append(self._meta(t, with_copy=False))
        row.append(body)
        self.append(row)

    # shared

    def _meta(self, t: Turn, with_copy: bool) -> Gtk.Box:
        meta = Gtk.Box(spacing=6)
        meta.add_css_class("ail-meta")
        if t.route in ROUTE_LABELS:
            meta.append(_chip(ROUTE_LABELS[t.route], f"route-{t.route}",
                              tooltip=f"Answered on the {ROUTE_HARDWARE[t.route]}"))
        parts: list[tuple[str, str | None]] = []
        if t.model:
            parts.append((t.model, "Model"))
        if t.confidence is not None:
            parts.append((f"{round(t.confidence * 100)}%", "Confidence"))
        total = total_latency(t)
        if total is not None:
            parts.append((format_seconds(total), latency_text(t) or None))
        if not self.compact:
            parts.append((format_time(t.ts), None))
        for i, (text, tip) in enumerate(parts):
            if i or t.route in ROUTE_LABELS:
                meta.append(_label("·", "dimmed", "caption"))
            meta.append(_label(text, "caption", "dimmed", "numeric", tooltip=tip))
        if t.status in STATUS_LABELS:
            meta.append(_chip(STATUS_LABELS[t.status], f"status-{t.status}"))
        if with_copy:
            btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy reply", valign=Gtk.Align.CENTER)
            for c in ("flat", "circular", "ail-copy"):
                btn.add_css_class(c)
            btn.update_property([Gtk.AccessibleProperty.LABEL], ["Copy reply"])
            btn.connect("clicked", self._on_copy)
            self._copy_btn = btn
            meta.append(btn)
        return meta

    def _on_copy(self, btn: Gtk.Button) -> None:
        if not copy_to_clipboard(self, turn_copy_text(self.turn)):
            return
        btn.set_icon_name("object-select-symbolic")
        btn.set_tooltip_text("Copied")
        if self._copy_reset:
            GLib.source_remove(self._copy_reset)

        def reset():
            self._copy_reset = 0
            btn.set_icon_name("edit-copy-symbolic")
            btn.set_tooltip_text("Copy reply")
            return False

        self._copy_reset = GLib.timeout_add(1400, reset)

    def _on_unrealize(self, *_):
        if self._copy_reset:
            GLib.source_remove(self._copy_reset)
            self._copy_reset = 0
            if self._copy_btn is not None:
                self._copy_btn.set_icon_name("edit-copy-symbolic")
