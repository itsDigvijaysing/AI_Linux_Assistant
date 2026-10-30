"""The "Battery and Charger" group: which settings are in use, and which ones the page below edits.

Pages that hold per-profile rows read and write through a ProfileView; this group switches the view between the
battery and charger profiles, and retitles the page's per-profile groups so it's clear which one is shown.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

USE = (("auto", "Switch with the Charger"), ("battery", "Battery Settings Always"),
       ("charger", "Charger Settings Always"))
NAMES = {"battery": "Battery", "charger": "Charger"}
SUFFIX = {"battery": "on Battery", "charger": "on the Charger"}


class ProfileGroup(Adw.PreferencesGroup):
    def __init__(self, view, description: str = ""):
        super().__init__(title="Battery and Charger", description=description or
                         "Battery and charger each keep their own settings for the sections marked below.")
        self.view = view
        self.base = view.base
        self._titled: list[tuple[Adw.PreferencesGroup, str]] = []
        self._syncing = False

        ids = [i for i, _ in USE]
        self._use = Adw.ComboRow(title="Settings in Use", use_subtitle=False,
                                 model=Gtk.StringList.new([label for _, label in USE]))
        self._use.add_prefix(Gtk.Image(icon_name="battery-symbolic"))
        current = str(self.base.get("profile"))
        self._use.set_selected(ids.index(current) if current in ids else 0)
        self._use.connect("notify::selected", self._on_use, ids)
        self.add(self._use)

        self._edit = Adw.ActionRow(title="Show Settings For", use_markup=False)
        self._edit.add_prefix(Gtk.Image(icon_name="document-edit-symbolic"))
        self._toggle = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for name in ("battery", "charger"):
            self._toggle.add(Adw.Toggle(name=name, label=NAMES[name]))
        self._toggle.set_active_name(view.profile)
        self._toggle.connect("notify::active-name", self._on_toggle)
        self._toggle.update_property([Gtk.AccessibleProperty.LABEL], ["Show settings for"])
        self._edit.add_suffix(self._toggle)
        self.add(self._edit)

        self._handlers = [
            (self.base, self.base.connect("profile-changed", lambda *_: self._sync())),
            (self.base, self.base.connect("changed::profile", lambda *_: self._sync())),
            (view, view.connect("selected", lambda *_: self._sync())),
        ]
        self._sync()

    def titled(self, group: Adw.PreferencesGroup, title: str) -> Adw.PreferencesGroup:
        """Marks a per-profile group: its title says which profile it shows."""
        self._titled.append((group, title))
        group.set_title(f"{title} {SUFFIX[self.view.profile]}")
        return group

    def _on_use(self, row, _pspec, ids) -> None:
        if self._syncing:
            return
        value = ids[row.get_selected()]
        if value != self.base.get("profile"):
            self.base.set("profile", value)
            if value != "auto":
                self.view.select(value)  # forcing a profile is usually followed by editing it

    def _on_toggle(self, toggle, _pspec) -> None:
        if not self._syncing and toggle.get_active_name() in NAMES:
            self.view.select(toggle.get_active_name())

    def _sync(self) -> None:
        self._syncing = True
        try:
            ids = [i for i, _ in USE]
            current = str(self.base.get("profile"))
            if current in ids:
                self._use.set_selected(ids.index(current))
            active, shown = self.base.active_profile(), self.view.profile
            on_ac = "on the charger" if active == "charger" else "on battery"
            self._use.set_subtitle(f"Now using the {NAMES[active].lower()} settings" +
                                   (f" (the laptop is {on_ac})" if current == "auto" else ""))
            self._toggle.set_active_name(shown)
            self._edit.set_subtitle("In use now" if shown == active else "Not in use now: changes apply when "
                                                                           f"the {NAMES[shown].lower()} settings are")
            for group, title in self._titled:
                group.set_title(f"{title} {SUFFIX[shown]}")
        finally:
            self._syncing = False

    def close(self) -> None:
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
