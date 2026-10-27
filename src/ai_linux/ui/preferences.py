"""Preferences dialog: startup, what the assistant knows about you, appearance, privacy and about."""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ai_linux import __version__, autostart  # noqa: E402
from ai_linux.config import APP_NAME, ICON_NAME, REPO_ROOT, ProfileView  # noqa: E402
from ai_linux.ui.profile_group import ProfileGroup  # noqa: E402

log = logging.getLogger(__name__)

LICENCES = (
    ("ai-linux", APP_NAME, "MIT licence", "LICENSE"),
    ("glados", "GLaDOS speech pipeline", "MIT licence, David Ng", "LICENSE.GLaDOS"),
    ("computer-use-linux", "computer-use-linux window control", "MIT licence", "LICENSE.computer-use-linux"),
)
THEMES = (("light", "Light"), ("system", "Follow System"))
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
        self._about_timer = 0
        self._profiles: ProfileGroup | None = None
        self._dependants: list[Gtk.Widget] = []
        for build in (self._general, self._appearance, self._privacy, self._about):
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
        group = Adw.PreferencesGroup(
            title="About You",
            description="The big model reads this when it answers or writes for you, for example to sign an "
                        "email. It stays on this computer.")
        name = Adw.EntryRow(title="Your name", show_apply_button=True, text=str(self.settings.get("user_name")))
        name.connect("apply", lambda r: self._store("user_name", r.get_text().strip()))
        self._watch("user_name", lambda: name.set_text(str(self.settings.get("user_name"))))
        group.add(name)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=8, bottom_margin=8,
                            left_margin=10, right_margin=10)
        view.update_property([Gtk.AccessibleProperty.LABEL], ["About you"])
        buf = view.get_buffer()
        buf.set_text(str(self.settings.get("about_me")))
        hint = Gtk.Label(label="Anything that helps, such as your city, work, languages or how you like replies. "
                               "Up to 1,500 characters.", xalign=0, wrap=True,
                         css_classes=["dim-label", "caption", "ail-sp-note"])

        def commit():
            self._about_timer = 0
            self._store("about_me", buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()[:1500])
            return False

        def changed(_b):
            if self._syncing:
                return
            if self._about_timer:
                GLib.source_remove(self._about_timer)
            self._about_timer = GLib.timeout_add(600, commit)  # one write per pause, not per key

        buf.connect("changed", changed)
        self._about_commit = commit
        self._watch("about_me", lambda: buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
                    != self.settings.get("about_me") and buf.set_text(str(self.settings.get("about_me"))))
        scroller = Gtk.ScrolledWindow(child=view, min_content_height=90, max_content_height=200,
                                      propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        group.add(Gtk.Frame(child=scroller, css_classes=["card"]))
        group.add(hint)
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
        style.add(self._toggles("theme", "Colours", "Follow System switches to dark when your desktop does", THEMES))
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
                                      "The internet is used only when you download a model.")
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

    # About

    def _about(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="About", icon_name="help-about-symbolic", name="about")
        hero_group = Adw.PreferencesGroup()
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER,
                       css_classes=["ail-prefs-hero"])
        hero.append(Gtk.Image(icon_name=ICON_NAME, pixel_size=96, css_classes=["icon-dropshadow"]))
        hero.append(Gtk.Label(label=APP_NAME, css_classes=["title-1"]))
        hero.append(Gtk.Label(label="A local-first voice assistant for the Linux desktop", wrap=True,
                              justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))
        version = Gtk.Label(label=f"Version {__version__}", halign=Gtk.Align.CENTER, selectable=True,
                            css_classes=["ail-prefs-version"])
        hero.append(version)
        hero_group.add(hero)
        page.add(hero_group)

        licences = Adw.PreferencesGroup(title="Licences",
                                        description="Free software. You may use, study, share and change it.")
        for key, title, subtitle, filename in LICENCES:
            path = REPO_ROOT / filename
            if not path.exists():
                continue
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, k=key: self.open_licence(k))
            licences.add(row)
        page.add(licences)
        return page

    def open_licence(self, key: str) -> None:
        entry = next((e for e in LICENCES if e[0] == key), None)
        if entry is None:
            return
        _key, title, _sub, filename = entry
        buf = Gtk.TextBuffer()
        buf.set_text("Loading…")
        text = Gtk.TextView(buffer=buf, editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            css_classes=["ail-prefs-licence"], monospace=True)
        scroller = Gtk.ScrolledWindow(child=text, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(Adw.HeaderBar())
        self.push_subpage(Adw.NavigationPage(title=title, child=view))

        def loaded(file, result):
            try:
                ok, data, _etag = file.load_contents_finish(result)
                buf.set_text(data.decode("utf-8", "replace"))
            except GLib.Error as exc:
                buf.set_text(f"Couldn't read {filename}: {exc.message}")

        Gio.File.new_for_path(str(REPO_ROOT / filename)).load_contents_async(None, loaded)

    def _on_closed(self, *_):
        if self._spin_timer:
            GLib.source_remove(self._spin_timer)
            self._spin_timer = 0
            self._store("history_retention_days", int(self._spin.get_value()))
        if self._about_timer:
            GLib.source_remove(self._about_timer)
            self._about_commit()
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
