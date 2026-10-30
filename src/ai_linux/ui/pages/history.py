"""History: saved turns grouped by day, searchable, with copy, run again and delete."""

from __future__ import annotations

import datetime as dt
import logging
import threading

from gi.repository import Adw, GLib, Gtk

from ai_linux.state import Turn
from ai_linux.ui.widgets.conversation import TurnView, copy_to_clipboard, turn_copy_text

log = logging.getLogger(__name__)

PAGE_SIZE = 200


def day_title(day: dt.date, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    delta = (today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if 1 < delta < 7:
        return day.strftime("%A")
    text = f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"
    return text if day.year == today.year else f"{text} {day.year}"


class _Section:
    """One day: a heading with its turn count and a boxed list of rows, newest first."""

    def __init__(self, day: dt.date):
        self.day = day
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=8)
        head.add_css_class("ail-day-header")
        title = Gtk.Label(label=day_title(day), xalign=0)
        title.add_css_class("heading")
        head.append(title)
        self.count = Gtk.Label(xalign=0, valign=Gtk.Align.BASELINE_CENTER)
        for c in ("caption", "dimmed", "numeric"):
            self.count.add_css_class(c)
        head.append(self.count)
        self.box.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.box.append(self.list)
        self.rows = 0

    def set_count(self, n: int) -> None:
        self.count.set_label(f"{n} request" if n == 1 else f"{n} requests")


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="History", tag="history")
        self.ctx = ctx
        self.store = ctx.history
        self._limit = PAGE_SIZE
        self._rows: dict[str, tuple[Gtk.ListBoxRow, TurnView, _Section]] = {}
        self._sections: dict[dt.date, _Section] = {}
        self._day_counts: dict[dt.date, int] = {}
        self._dirty = True
        self._reload_id = 0

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._clear_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Clear History")
        self._clear_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Clear History"])
        self._clear_btn.connect("clicked", self._confirm_clear)
        header.pack_end(self._clear_btn)
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._search = Gtk.SearchEntry(placeholder_text="Search what you said and what you heard back",
                                       hexpand=True)
        self._search.connect("search-changed", lambda *_: self._reload(reset=True))
        self._search.connect("stop-search", lambda e: e.set_text(""))
        self._search.set_key_capture_widget(self)
        search_clamp = Adw.Clamp(maximum_size=820, tightening_threshold=600, child=self._search)
        search_clamp.set_margin_top(12)
        search_clamp.set_margin_bottom(4)
        search_clamp.set_margin_start(16)
        search_clamp.set_margin_end(16)
        content.append(search_clamp)

        self._days_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._more = Gtk.Button(label="Show Older Requests", halign=Gtk.Align.CENTER, visible=False)
        self._more.add_css_class("pill")
        self._more.set_margin_top(20)
        self._more.connect("clicked", self._show_more)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.set_margin_bottom(28)
        inner.set_margin_start(16)
        inner.set_margin_end(16)
        inner.append(self._days_box)
        inner.append(self._more)
        clamp = Adw.Clamp(maximum_size=820, tightening_threshold=600, child=inner)
        self._scroller = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._stack.add_named(self._scroller, "list")
        self._stack.add_named(Adw.StatusPage(
            icon_name="document-open-recent-symbolic", title="No History Yet",
            description="Your conversations with the assistant show up here, grouped by day.",
        ), "empty")
        self._stack.add_named(Adw.StatusPage(
            icon_name="system-search-symbolic", title="No Results", description="Try different words.",
        ), "noresults")
        self._stack.add_named(Adw.StatusPage(
            icon_name="dialog-warning-symbolic", title="History Is Unavailable",
            description="The history database could not be opened. Details are in the app log.",
        ), "unavailable")
        content.append(self._stack)
        toolbar.set_content(content)
        self.set_child(toolbar)

        if self.store is not None and hasattr(self.store, "add_listener"):
            self.store.add_listener(self._on_store_event)
        self.connect("map", lambda *_: self._dirty and self._reload())
        self.connect("destroy", self._on_destroy)
        self._reload()

    # public, used by the window's Ctrl+F

    def focus_search(self) -> None:
        self._search.grab_focus()

    # loading

    def _reload(self, reset: bool = False) -> None:
        if self._reload_id:
            GLib.source_remove(self._reload_id)
            self._reload_id = 0
        if reset:
            self._limit = PAGE_SIZE
        if self.store is None:
            self._stack.set_visible_child_name("unavailable")
            self._clear_btn.set_sensitive(False)
            self._search.set_sensitive(False)
            return
        text = self._search.get_text().strip()
        adj = self._scroller.get_vadjustment()
        keep = 0.0 if reset else adj.get_value()
        try:
            turns = self.store.search(text, self._limit) if text else self.store.recent(self._limit)
            self._day_counts = {} if text else dict(self.store.days())
            total = self.store.count()
        except Exception:
            log.exception("loading history")
            self._stack.set_visible_child_name("unavailable")
            return

        self._clear_sections()
        for turn in turns:  # newest first
            self._append(turn)
        self._update_counts()
        self._stack.set_visible_child_name("list" if turns else ("noresults" if text else "empty"))
        self._more.set_visible(not text and len(turns) >= self._limit)
        self._clear_btn.set_sensitive(total > 0)
        self._dirty = False
        GLib.idle_add(lambda: adj.set_value(keep) and False)

    def _schedule_reload(self) -> None:
        if not self._reload_id:
            self._reload_id = GLib.timeout_add(150, lambda: self._reload() and False)

    def _show_more(self, *_):
        self._limit += PAGE_SIZE
        self._reload()

    def _clear_sections(self) -> None:
        child = self._days_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._days_box.remove(child)
            child = nxt
        self._sections.clear()
        self._rows.clear()

    def _section_for(self, day: dt.date, at_top: bool = False) -> _Section:
        section = self._sections.get(day)
        if section is None:
            section = _Section(day)
            self._sections[day] = section
            if at_top:
                self._days_box.prepend(section.box)
            else:
                self._days_box.append(section.box)
        return section

    def _append(self, turn: Turn, at_top: bool = False) -> None:
        day = dt.date.fromtimestamp(turn.ts)
        section = self._section_for(day, at_top)
        row, view = self._make_row(turn)
        if at_top:
            section.list.prepend(row)
        else:
            section.list.append(row)
        section.rows += 1
        self._rows[turn.id] = (row, view, section)

    def _update_counts(self) -> None:
        for day, section in self._sections.items():
            section.set_count(max(self._day_counts.get(day, 0), section.rows))

    def _make_row(self, turn: Turn) -> tuple[Gtk.ListBoxRow, TurnView]:
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=8)
        box.add_css_class("ail-history-row")
        view = TurnView(turn, compact=True)
        view.set_hexpand(True)
        box.append(view)
        actions = Gtk.Box(spacing=2, valign=Gtk.Align.START)
        actions.add_css_class("ail-row-actions")
        for icon, tip, cb in (
            ("edit-copy-symbolic", "Copy reply", self._copy),
            ("media-playlist-repeat-symbolic", "Run again", self._rerun),
            ("user-trash-symbolic", "Delete", self._delete),
        ):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.update_property([Gtk.AccessibleProperty.LABEL], [tip])
            btn.connect("clicked", lambda _b, cb=cb, tid=turn.id: self._run_action(cb, tid))
            actions.append(btn)
        box.append(actions)
        row.set_child(box)
        return row, view

    # live changes

    def _on_store_event(self, event: str, turn_id: str | None) -> None:
        # The store calls back on the writer's thread.
        if threading.current_thread() is not threading.main_thread():
            GLib.idle_add(lambda: self._on_store_event(event, turn_id) and False)
            return
        try:
            self._apply_event(event, turn_id)
        except Exception:
            log.exception("history event %s", event)
            self._schedule_reload()

    def _apply_event(self, event: str, turn_id: str | None) -> None:
        if not self.get_mapped():
            self._dirty = True
            return
        if event in ("clear", "prune") or self._search.get_text().strip() or not turn_id:
            self._schedule_reload()
            return
        if event == "delete":
            self._remove_row(turn_id)
            return
        turn = self.store.get(turn_id)
        if turn is None:
            return
        found = self._rows.get(turn_id)
        if found is not None:
            found[1].update(turn)
            return
        day = dt.date.fromtimestamp(turn.ts)
        if self._sections and day < max(self._sections):
            self._schedule_reload()  # an older turn came back (undo): let a reload place it
            return
        self._day_counts[day] = self._day_counts.get(day, 0) + 1
        self._append(turn, at_top=True)
        self._update_counts()
        self._stack.set_visible_child_name("list")
        self._clear_btn.set_sensitive(True)

    def _remove_row(self, turn_id: str) -> None:
        found = self._rows.pop(turn_id, None)
        if found is None:
            return
        row, _view, section = found
        section.list.remove(row)
        section.rows -= 1
        if section.day in self._day_counts:
            self._day_counts[section.day] = max(0, self._day_counts[section.day] - 1)
        if section.rows <= 0:
            self._days_box.remove(section.box)
            self._sections.pop(section.day, None)
        self._update_counts()
        if not self._rows:
            self._schedule_reload()  # picks the right empty state and older pages

    # actions

    def _run_action(self, cb, turn_id: str) -> None:
        found = self._rows.get(turn_id)
        if found is None:
            return
        try:
            cb(found[1].turn)
        except Exception:
            log.exception("history action")

    def _toast(self, toast: Adw.Toast) -> None:
        root = self.get_root()
        if hasattr(root, "add_toast"):
            root.add_toast(toast)

    def _copy(self, turn: Turn) -> None:
        if copy_to_clipboard(self, turn_copy_text(turn)):
            self._toast(Adw.Toast(title="Copied to clipboard", timeout=2))

    def _rerun(self, turn: Turn) -> None:
        if not turn.heard:
            return
        self.ctx.controller.submit_text(turn.heard)
        root = self.get_root()
        if hasattr(root, "show_page"):
            root.show_page("home")  # the reply appears in today's conversation

    def _delete(self, turn: Turn) -> None:
        if not self.store.delete(turn.id):
            return
        toast = Adw.Toast(title="Request deleted", button_label="Undo", timeout=5)
        toast.connect("button-clicked", lambda *_: self._undo_delete(turn))
        self._toast(toast)

    def _undo_delete(self, turn: Turn) -> None:
        try:
            self.store.add(turn)
        except Exception:
            log.exception("restoring turn")

    def _confirm_clear(self, *_):
        n = self.store.count() if self.store is not None else 0
        dialog = Adw.AlertDialog(
            heading="Clear All History?",
            body=f"This deletes all {n} saved requests on this computer. It cannot be undone." if n != 1
            else "This deletes the saved request on this computer. It cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear History")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_response)
        dialog.present(self.get_root())

    def _on_clear_response(self, _dialog, response: str) -> None:
        if response != "clear" or self.store is None:
            return
        try:
            n = self.store.clear()
        except Exception:
            log.exception("clearing history")
            self._toast(Adw.Toast(title="Could not clear the history"))
            return
        self._reload(reset=True)
        self._toast(Adw.Toast(title=f"Deleted {n} requests" if n != 1 else "Deleted 1 request", timeout=3))

    def _on_destroy(self, *_):
        if self._reload_id:
            GLib.source_remove(self._reload_id)
            self._reload_id = 0
        if self.store is not None and hasattr(self.store, "remove_listener"):
            self.store.remove_listener(self._on_store_event)
