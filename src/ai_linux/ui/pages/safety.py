"""Safety page: arm actions, see what is gated, test a command against the block list, recent actions."""

from __future__ import annotations

import datetime as dt
import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux.safety.denylist import destructive_reason  # noqa: E402
from ai_linux.tools_catalog import lookup  # noqa: E402

log = logging.getLogger(__name__)

RECENT_LIMIT = 50
_PAGE_TURNS = 200  # turns per history query while collecting tool calls
_MAX_TURNS = 2000  # stop scanning older history after this many turns
_CHECK_DELAY_MS = 120

# verdict -> (tag text, tag kind, icon kind)
_VERDICTS = {
    "ran": ("Ran", "success", ""),
    "blocked": ("Blocked", "error", "error"),
    "denied": ("Not allowed", "warning", "neutral"),
    "needs_confirm": ("Waiting", "", ""),
    "error": ("Failed", "error", "error"),
}


def _tag(text: str, kind: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER, css_classes=["ail-sp-tag"])
    if kind:
        label.add_css_class(kind)
    return label


def _set_tag(label: Gtk.Label, text: str, kind: str) -> None:
    label.set_label(text)
    for k in ("success", "warning", "error", "neutral"):
        label.remove_css_class(k)
    if kind:
        label.add_css_class(kind)


def _icon_tile(icon: str, kind: str = "") -> Gtk.Image:
    image = Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["ail-sp-icon"])
    if kind:
        image.add_css_class(kind)
    return image


def when_text(ts: float, now: dt.datetime | None = None) -> str:
    """Short time for a row: "Just now", "5 min ago", "14:05", "Yesterday 14:05", "3 Oct 14:05"."""
    now = now or dt.datetime.now()
    then = dt.datetime.fromtimestamp(ts)
    secs = (now - then).total_seconds()
    if 0 <= secs < 60:
        return "Just now"
    if 0 <= secs < 3600:
        return f"{int(secs // 60)} min ago"
    clock = then.strftime("%H:%M")
    days = (now.date() - then.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"Yesterday {clock}"
    text = f"{then.day} {then.strftime('%b')}"
    return f"{text} {clock}" if then.year == now.year else f"{text} {then.year}"


def outcome(call) -> tuple[str, str, str]:
    """Tag text, tag kind and icon kind for a tool call, folding a failing exit code into the verdict."""
    verdict = getattr(call, "verdict", "ran") or "ran"
    text, kind, icon = _VERDICTS.get(verdict, (verdict.replace("_", " ").capitalize(), "neutral", "neutral"))
    code = getattr(call, "exit_code", None)
    if verdict == "ran" and code not in (None, 0):
        return f"Exit {code}", "warning", ""
    return text, kind, icon


def collect_calls(history, limit: int = RECENT_LIMIT) -> list[tuple[float, object]]:
    """Newest tool calls first as (turn ts, ToolCall), paging back through history until ``limit``."""
    calls: list[tuple[float, object]] = []
    before = None
    scanned = 0
    while len(calls) < limit and scanned < _MAX_TURNS:
        turns = history.recent(_PAGE_TURNS, before=before)
        if not turns:
            break
        for turn in turns:
            for call in reversed(turn.tools or []):  # the last call of a turn is the newest
                calls.append((turn.ts, call))
                if len(calls) >= limit:
                    return calls
        scanned += len(turns)
        before = turns[-1].ts
        if len(turns) < _PAGE_TURNS:
            break
    return calls


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Safety", tag="safety")
        self.ctx = ctx
        self.settings = ctx.settings
        self.history = getattr(ctx, "history", None)
        self._handlers: list[tuple[object, int]] = []
        self._check_id = 0
        self._reload_id = 0
        self._loading = False
        self._reload_again = False
        self._alive = True
        self._syncing = False

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._toasts = Adw.ToastOverlay()
        prefs = Adw.PreferencesPage()
        prefs.add(self._build_actions())
        prefs.add(self._build_gated())
        prefs.add(self._build_checker())
        prefs.add(self._build_recent())
        self._toasts.set_child(prefs)
        view.set_content(self._toasts)
        self.set_child(view)

        for key in ("actions_armed", "confirm_shell"):
            self._handlers.append((self.settings, self.settings.connect(f"changed::{key}", self._on_setting)))
        if self.history is not None and hasattr(self.history, "add_listener"):
            self.history.add_listener(self._on_history)
        self.connect("destroy", self._on_destroy)
        self._sync()
        self._reload()

    # Actions

    def _build_actions(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Actions")
        self._armed_row = Adw.SwitchRow(
            title="Allow actions",
            subtitle="Let the assistant open apps, change settings and run commands. "
                     "When off, it only answers.",
        )
        self._armed_row.add_prefix(_icon_tile("security-high-symbolic"))
        self._armed_row.connect("notify::active", self._on_switch, "actions_armed")
        group.add(self._armed_row)

        self._confirm_row = Adw.SwitchRow(
            title="Ask before running shell commands",
            subtitle="Show the exact command and wait for your yes before it runs",
        )
        self._confirm_row.add_prefix(_icon_tile("utilities-terminal-symbolic"))
        self._confirm_row.connect("notify::active", self._on_switch, "confirm_shell")
        group.add(self._confirm_row)
        return group

    def _on_switch(self, row, _pspec, key: str) -> None:
        if self._syncing:
            return
        try:
            self.settings.set(key, row.get_active())
        except Exception:
            log.exception("saving %s", key)
            self._toasts.add_toast(Adw.Toast(title="Could not save the setting"))
            self._sync()

    def _on_setting(self, *_args) -> None:
        if self._alive:
            self._sync()
            if self._cmd_row.get_text().strip():
                self._run_check()  # the allowed hint mentions arming and confirmation

    def _sync(self) -> None:
        armed = bool(self.settings.get("actions_armed"))
        confirm = bool(self.settings.get("confirm_shell"))
        self._syncing = True
        self._armed_row.set_active(armed)
        self._confirm_row.set_active(confirm)
        self._syncing = False
        self._confirm_row.set_sensitive(armed)
        if armed:
            _set_tag(self._shell_tag, "Asks first" if confirm else "Allowed", "" if confirm else "success")
            _set_tag(self._desktop_tag, "Allowed", "success")
        else:
            _set_tag(self._shell_tag, "Off", "neutral")
            _set_tag(self._desktop_tag, "Off", "neutral")
        _set_tag(self._computer_tag, "Allowed" if armed else "Off", "success" if armed else "neutral")

    # What is gated

    def _build_gated(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="What is gated",
            description="These run only while actions are allowed. Questions, the time and the battery "
                        "level always work.",
        )
        shell = Adw.ActionRow(title="Shell commands",
                              subtitle="Any command the assistant writes and runs in a terminal shell")
        shell.add_prefix(_icon_tile("system-run-symbolic"))
        self._shell_tag = _tag("")
        shell.add_suffix(self._shell_tag)
        group.add(shell)

        desktop = Adw.ActionRow(title="Desktop actions",
                                subtitle="Volume, brightness, screenshots, media, opening apps, files and links")
        desktop.add_prefix(_icon_tile("view-app-grid-symbolic"))
        self._desktop_tag = _tag("")
        desktop.add_suffix(self._desktop_tag)
        group.add(desktop)

        computer = Adw.ActionRow(title="Computer use",
                                 subtitle="Clicking and typing in other windows, when that tool is turned on")
        computer.add_prefix(_icon_tile("input-mouse-symbolic"))
        self._computer_tag = _tag("")
        computer.add_suffix(self._computer_tag)
        group.add(computer)

        floor = Adw.ActionRow(
            title="Catastrophic commands are always blocked",
            subtitle="Wiping your home folder or a whole disk, formatting drives and piping downloads into "
                     "a shell are refused, even with actions allowed",
            css_classes=["ail-sf-floor"],
        )
        floor.add_prefix(_icon_tile("action-unavailable-symbolic", "error"))
        floor.add_suffix(_tag("Always on", "error"))
        group.add(floor)
        return group

    # Test a command

    def _build_checker(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Test a command",
            description="Check a command against the block list. Nothing is run.",
        )
        self._cmd_row = Adw.EntryRow(title="Command to check", css_classes=["ail-sf-command"])
        self._cmd_row.add_prefix(Gtk.Image(icon_name="utilities-terminal-symbolic"))
        self._cmd_row.connect("changed", self._on_command_changed)
        self._cmd_row.connect("entry-activated", lambda *_: self._run_check())
        group.add(self._cmd_row)

        self._result_row = Adw.ActionRow(css_classes=["ail-sp-result", "idle"], use_markup=False)
        self._result_icon = Gtk.Image(icon_name="dialog-question-symbolic", css_classes=["ail-sp-result-icon"])
        self._result_row.add_prefix(self._result_icon)
        group.add(self._result_row)
        self._show_result(None, None)
        return group

    def _on_command_changed(self, _row) -> None:
        if self._check_id:
            GLib.source_remove(self._check_id)
        self._check_id = GLib.timeout_add(_CHECK_DELAY_MS, self._run_check)

    def _run_check(self) -> bool:
        if self._check_id:
            GLib.source_remove(self._check_id)
            self._check_id = 0
        command = self._cmd_row.get_text()
        if not command.strip():
            self._show_result(None, None)
            return GLib.SOURCE_REMOVE
        try:
            reason = destructive_reason(command)
        except Exception:
            log.exception("checking %r", command)
            reason = "could not be checked, so it would not run"
        self._show_result(command, reason)
        return GLib.SOURCE_REMOVE

    def _show_result(self, command: str | None, reason: str | None) -> None:
        row = self._result_row
        for kind in ("idle", "allowed", "blocked"):
            row.remove_css_class(kind)
        if command is None:
            row.add_css_class("idle")
            row.set_title("Type a command above")
            row.set_subtitle("For example: rm -rf ~/Downloads/old")
            self._result_icon.set_from_icon_name("dialog-question-symbolic")
        elif reason:
            row.add_css_class("blocked")
            row.set_title("Blocked")
            row.set_subtitle(reason[:1].upper() + reason[1:])
            self._result_icon.set_from_icon_name("action-unavailable-symbolic")
        else:
            row.add_css_class("allowed")
            row.set_title("Allowed")
            armed = bool(self.settings.get("actions_armed"))
            row.set_subtitle("Not on the block list. It still runs only while actions are allowed"
                             + (", after you confirm it" if armed and self.settings.get("confirm_shell") else ""))
            self._result_icon.set_from_icon_name("object-select-symbolic")

    # Recent actions

    def _build_recent(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Recent actions",
            description=f"The last {RECENT_LIMIT} tools the assistant called, newest first",
        )
        self._recent = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        group.add(self._recent)
        return group

    def _on_history(self, _event, _turn_id=None) -> None:
        # listeners run on the writer's thread; coalesce bursts on the main loop
        def schedule():
            if self._alive and not self._reload_id:
                self._reload_id = GLib.timeout_add(250, self._reload_timeout)
            return False

        GLib.idle_add(schedule)

    def _reload_timeout(self) -> bool:
        self._reload_id = 0
        self._reload()
        return GLib.SOURCE_REMOVE

    def _reload(self) -> None:
        if self.history is None:
            self._fill_empty("History is off", "Recent actions appear here when conversation history is saved.")
            return
        if self._loading:
            self._reload_again = True
            return
        self._loading = True
        history = self.history

        def work():
            try:
                calls, error = collect_calls(history), None
            except Exception as exc:
                log.exception("loading recent actions")
                calls, error = [], str(exc)
            GLib.idle_add(self._loaded, calls, error)

        threading.Thread(target=work, name="SafetyRecent", daemon=True).start()

    def _loaded(self, calls, error) -> bool:
        self._loading = False
        if not self._alive:
            return False
        if self._reload_again:
            self._reload_again = False
            self._reload()
        self._recent.remove_all()
        if error:
            self._fill_empty("Could not read history", error)
        elif not calls:
            self._fill_empty("No actions yet",
                             "Commands and desktop actions the assistant runs will show here.")
        else:
            now = dt.datetime.now()
            for ts, call in calls:
                try:
                    self._recent.append(self._call_row(ts, call, now))
                except Exception:
                    log.exception("recent action row")
        return False

    def _fill_empty(self, title: str, subtitle: str) -> None:
        self._recent.remove_all()
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False, activatable=False,
                            css_classes=["ail-sp-empty"])
        row.add_prefix(_icon_tile("document-open-recent-symbolic", "neutral"))
        self._recent.append(row)

    def _call_row(self, ts: float, call, now: dt.datetime) -> Adw.ActionRow:
        info = lookup(call.name)
        name = info.title if info else call.name
        command = (call.command or "").strip()
        text, kind, icon_kind = outcome(call)
        title = command or name
        detail = [name] if command else []
        output = (call.output or "").strip()
        if call.verdict == "blocked" and output:
            detail = [output[:1].upper() + output[1:]]  # the block reason says more than the tool name
        detail.append(when_text(ts, now))
        row = Adw.ActionRow(title=title, subtitle=" · ".join(detail), use_markup=False,
                            title_lines=2, subtitle_lines=1)
        if command:
            title_label = _find_title(row)
            if title_label is not None:
                title_label.add_css_class("ail-sf-command-title")
        icon = info.icon if info else "application-x-executable-symbolic"
        row.add_prefix(_icon_tile("action-unavailable-symbolic" if call.verdict == "blocked" else icon, icon_kind))
        row.add_suffix(_tag(text, kind))
        if output:
            row.set_tooltip_text(output[:600])
        return row

    # lifecycle

    def _on_destroy(self, *_args) -> None:
        self._alive = False
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        if self.history is not None and hasattr(self.history, "remove_listener"):
            self.history.remove_listener(self._on_history)
        for attr in ("_check_id", "_reload_id"):
            source = getattr(self, attr)
            if source:
                GLib.source_remove(source)
                setattr(self, attr, 0)


def _find_title(row: Gtk.Widget) -> Gtk.Label | None:
    """The row's title label, so a command can use a monospace face without touching the subtitle."""
    stack = [row]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label) and widget.has_css_class("title"):
            return widget
        child = widget.get_last_child()
        while child is not None:
            stack.append(child)
            child = child.get_prev_sibling()
    return None
