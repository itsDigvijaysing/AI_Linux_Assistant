"""Preferences dialog: startup, what the assistant knows about you, appearance and privacy (About is its own window)."""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux import autostart  # noqa: E402
from ai_linux.config import ProfileView  # noqa: E402
from ai_linux.ui.profile_group import ProfileGroup  # noqa: E402

log = logging.getLogger(__name__)

THEMES = (("light", "Light"), ("dark", "Dark"), ("system", "Follow System"))
POSITIONS = (("bottom", "Bottom"), ("top", "Top"))


class _Bindings:
    """Settings handlers dropped when the dialog closes, so a closed dialog is never updated."""

    def __init__(self):
        self._ids: list[tuple[object, int]] = []

    def connect(self, obj, signal: str, cb) -> None:
        self._ids.append((obj, obj.connect(signal, cb)))

    def clear(self) -> None:
        for obj, hid in self._ids:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._ids.clear()


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, parent, ctx):
        super().__init__(title="Preferences", search_enabled=True)
        self.parent_window = parent
        self.ctx = ctx
        self.settings = ProfileView(ctx.settings)
        self._binds = _Bindings()
        self._syncing = False
        self._spin_timer = 0
        self._profiles: ProfileGroup | None = None
        self._dependants: list[Gtk.Widget] = []
        for build in (self._general, self._appearance, self._privacy):
            try:
                self.add(build())
            except Exception:
                log.exception("building preferences page %s", build.__name__)
        self.connect("closed", self._on_closed)

    # helpers

    def _store(self, key: str, value) -> None:
        """Write only real user changes, so defaults are never persisted by accident."""
        if self._syncing or self.settings.get(key) == value:
            return
        try:
            self.settings.set(key, value)
        except Exception:
            log.exception("saving %s", key)

    def _watch(self, key: str, cb) -> None:
        def on_changed(*_):
            self._syncing = True
            try:
                cb()
            except Exception:
                log.exception("updating row for %s", key)
            finally:
                self._syncing = False

        self._binds.connect(self.settings, f"changed::{key}", on_changed)

    def _switch(self, key: str, title: str, subtitle: str) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(self.settings.get(key)))
        row.connect("notify::active", lambda r, _p: self._store(key, r.get_active()))
        self._watch(key, lambda: row.set_active(bool(self.settings.get(key))))
        return row

    def _toggles(self, key: str, title: str, subtitle: str, options) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        group = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for name, label in options:
            group.add(Adw.Toggle(name=name, label=label))
        group.set_active_name(str(self.settings.get(key)))
        group.connect("notify::active-name", lambda g, _p: g.get_active_name() and self._store(key, g.get_active_name()))
        self._watch(key, lambda: group.set_active_name(str(self.settings.get(key))))
        row.add_suffix(group)
        return row

    def _toast(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=text, timeout=3))

    # General

    def _general(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="General", icon_name="emblem-system-symbolic", name="general")
        startup = Adw.PreferencesGroup(title="Startup")
        self._autostart_row = Adw.SwitchRow(title="Start at login",
                                            subtitle="Open quietly in the background when you sign in",
                                            active=self._autostart_active())
        self._autostart_row.connect("notify::active", self._on_autostart)
        startup.add(self._autostart_row)
        startup.add(self._switch("keep_running", "Keep running when the window is closed",
                                 "Stay ready from the tray and the shortcut"))
        page.add(startup)

        tour = Adw.PreferencesGroup(title="Welcome Tour")
        row = Adw.ActionRow(title="Show the welcome tour again",
                            subtitle="Microphone check, models and how it listens")
        row.add_prefix(Gtk.Image(icon_name="starred-symbolic"))
        btn = Gtk.Button(label="Show", valign=Gtk.Align.CENTER)
        btn.connect("clicked", self._on_tour)
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        tour.add(row)
        page.add(tour)
        page.add(self._build_about_you())
        return page

    def _build_about_you(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="About You")
        row = Adw.ActionRow(title="Knowledge", activatable=True,
                            subtitle="Your name, facts you keep and what Phoenix has learned about you")
        row.add_prefix(Gtk.Image(icon_name="user-info-symbolic"))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self.ctx.app.activate_action("knowledge", None))
        group.add(row)
        return group

    def _autostart_active(self) -> bool:
        if self.ctx.screenshot_mode:
            return bool(self.settings.get("autostart"))
        try:
            return autostart.is_enabled()
        except Exception:
            log.exception("reading autostart state")
            return bool(self.settings.get("autostart"))

    def _on_autostart(self, row, _pspec) -> None:
        if self._syncing:
            return
        on = row.get_active()
        if not self.ctx.screenshot_mode:
            try:
                autostart.set_enabled(on)
            except OSError as exc:
                log.warning("autostart: %s", exc)
                self._toast(f"Couldn't change start at login: {exc.strerror or exc}")
                self._syncing = True
                row.set_active(not on)
                self._syncing = False
                return
        try:
            self.settings.set("autostart", on)  # a real toggle is a choice even when it equals the default
        except Exception:
            log.exception("saving autostart")

    def _on_tour(self, *_):
        try:
            self.settings.reset("onboarding_done")
            from ai_linux.ui import onboarding
        except Exception:
            log.exception("welcome tour unavailable")
            self._toast("The welcome tour isn't available")
            return
        parent, ctx = self.parent_window, self.ctx
        self.close()
        GLib.idle_add(lambda: onboarding.maybe_show(parent, ctx) and False)

    # Appearance

    def _appearance(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Appearance", icon_name="preferences-desktop-appearance-symbolic",
                                   name="appearance")
        style = Adw.PreferencesGroup(title="Style")
        style.add(self._toggles("theme", "Colours", "The window and the floating indicator; Follow System matches "
                                                    "your desktop", THEMES))
        style.add(self._switch("live_text", "Show words while talking",
                               "Your words as you speak, on Home and in the indicator; the indicator also shows "
                               "the reply"))
        page.add(style)
        self._profiles = ProfileGroup(self.settings, "The floating indicator can differ on battery and on the "
                                                     "charger.")
        page.add(self._profiles)

        pill = Adw.PreferencesGroup(
            title="Floating Indicator",
            description="A small waveform that shows on top of other windows while the assistant listens, "
                        "thinks or speaks.")
        show = self._switch("show_pill", "Show the floating indicator", "Only while the assistant is active")
        always = self._switch("pill_always", "Always show it", "Keep a small resting capsule on screen when idle")
        position = self._toggles("pill_position", "Position", "Where it sits on the screen", POSITIONS)
        for row in (show, always, position):
            pill.add(row)
        self._dependants = [always, position]
        self._sync_pill_rows()
        self._watch("show_pill", self._sync_pill_rows)
        page.add(self._profiles.titled(pill, "Floating Indicator"))
        return page

    def _sync_pill_rows(self) -> None:
        on = bool(self.settings.get("show_pill"))
        for row in self._dependants:
            row.set_sensitive(on)

    # Privacy

    def _privacy(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Privacy", icon_name="security-high-symbolic", name="privacy")
        local = Adw.PreferencesGroup()
        note = Adw.ActionRow(title="Everything stays on this computer",
                             subtitle="Your voice, conversations and models are processed here and never uploaded. "
                                      "The internet is used to download models, and by Web Access, Todoist "
                                      "and servers you add, once you turn them on.")
        note.add_prefix(Gtk.Image(icon_name="security-high-symbolic", pixel_size=24,
                                  css_classes=["ail-prefs-note-icon"]))
        local.add(note)
        page.add(local)

        history = Adw.PreferencesGroup(title="History")
        days = int(self.settings.get("history_retention_days") or 90)
        self._spin = Adw.SpinRow(title="Keep history for", subtitle="Days. Older conversations are deleted.",
                                 adjustment=Gtk.Adjustment(lower=1, upper=3650, step_increment=1,
                                                           page_increment=30, value=max(1, min(3650, days))),
                                 numeric=True, climb_rate=1)
        self._spin.connect("notify::value", self._on_retention)
        self._watch("history_retention_days",
                    lambda: self._spin.set_value(int(self.settings.get("history_retention_days") or 90)))
        history.add(self._spin)

        self._clear_row = Adw.ActionRow(title="Clear history")
        self._clear_btn = Gtk.Button(label="Clear…", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        self._clear_btn.connect("clicked", self._confirm_clear)
        self._clear_row.add_suffix(self._clear_btn)
        history.add(self._clear_row)
        page.add(history)
        self._refresh_count()
        return page

    def _on_retention(self, row, _pspec) -> None:
        if self._syncing:
            return
        # Holding the arrow fires many changes; save once it settles.
        if self._spin_timer:
            GLib.source_remove(self._spin_timer)

        def save():
            self._spin_timer = 0
            self._store("history_retention_days", int(row.get_value()))
            return GLib.SOURCE_REMOVE

        self._spin_timer = GLib.timeout_add(400, save)

    def _refresh_count(self) -> None:
        history = getattr(self.ctx, "history", None)
        if history is None:
            self._clear_row.set_subtitle("History isn't available right now")
            self._clear_btn.set_sensitive(False)
            return
        self._clear_row.set_subtitle("Delete every saved conversation")

        def work():
            try:
                n = history.count()
            except Exception:
                log.exception("counting history")
                return
            GLib.idle_add(self._show_count, n)

        threading.Thread(target=work, name="history-count", daemon=True).start()

    def _show_count(self, n: int) -> bool:
        if n == 0:
            self._clear_row.set_subtitle("Nothing saved yet")
            self._clear_btn.set_sensitive(False)
        else:
            self._clear_row.set_subtitle(f"{n} conversation{'s' if n != 1 else ''} saved on this computer")
            self._clear_btn.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _confirm_clear(self, *_):
        dialog = Adw.AlertDialog(heading="Clear all history?",
                                 body="Every saved conversation is deleted from this computer. This can't be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear History")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_response)
        dialog.present(self)

    def _on_clear_response(self, _dialog, response: str) -> None:
        if response != "clear":
            return
        history = self.ctx.history
        self._clear_btn.set_sensitive(False)

        def work():
            try:
                history.clear()
                ok = True
            except Exception:
                log.exception("clearing history")
                ok = False
            GLib.idle_add(self._cleared, ok)

        threading.Thread(target=work, name="history-clear", daemon=True).start()

    def _cleared(self, ok: bool) -> bool:
        self._toast("History cleared" if ok else "Couldn't clear the history")
        self._refresh_count()
        return GLib.SOURCE_REMOVE

    def _on_closed(self, *_):
        if self._spin_timer:
            GLib.source_remove(self._spin_timer)
            self._spin_timer = 0
            self._store("history_retention_days", int(self._spin.get_value()))
        self._binds.clear()
        if self._profiles is not None:
            self._profiles.close()
        self.settings.close()


def show_preferences(parent, ctx) -> PreferencesDialog | None:
    try:
        dialog = PreferencesDialog(parent, ctx)
        dialog.present(parent)
        return dialog
    except Exception:
        log.exception("preferences dialog failed")
        return None
