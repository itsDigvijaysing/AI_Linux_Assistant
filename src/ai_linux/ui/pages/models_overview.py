"""Two groups at the top of the Models page: what is running right now, and the only two settings that differ on
battery and on the charger (Speed, and whether the small model picks commands), side by side."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ai_linux import models_catalog  # noqa: E402
from ai_linux.schema import PROFILES, SPEED_LABELS, SPEEDS, loading  # noqa: E402

log = logging.getLogger(__name__)

NAMES = {"battery": "Battery", "charger": "Charger"}
WHERE = {"GPU": "Intel GPU", "CPU": "processor", "gpu": "NVIDIA GPU", "cpu": "processor",
         "intel-gpu": "Intel GPU"}
# (key, row title, row hint, options or None for a switch)
COLUMNS = (
    ("speed", "Speed", "How long the big model stays ready", [(i, label) for i, label, _ in SPEEDS]),
    ("small_on", "Small model picks commands", "On keeps the NVIDIA GPU asleep; off is more accurate", None),
)


def model_name(model_id: str) -> str:
    spec = models_catalog.get(model_id)
    return spec.name if spec else model_id


def small_in_use(settings) -> bool:
    return bool(settings.get("small_on")) and str(settings.get("small_model") or "none") not in ("none", "")


def running_rows(settings, ollama_status: dict) -> list[tuple[str, str, str]]:
    """(icon, title, subtitle) for "Running Now", from the settings in use and Ollama's loaded models."""
    active = settings.active_profile()
    speed = str(settings.get("speed"))
    where = "the laptop is on the charger" if active == "charger" else "the laptop is on battery"
    rows = [("battery-symbolic", f"{NAMES[active]} settings, {SPEED_LABELS.get(speed, speed)}",
             f"Chosen by the charger: {where}" if settings.get("profile") == "auto"
             else f"Always the {active} settings")]
    rows.append(("audio-input-microphone-symbolic", "Speech recognition",
                 f"{model_name(settings.get('stt_engine'))} on the {WHERE.get(settings.get('stt_device'), 'processor')}"))
    small = str(settings.get("small_model") or "none")
    if small_in_use(settings):
        sub = (f"{model_name(small)} on the {WHERE.get(settings.get('small_device'), 'Intel GPU')}, picks the "
               "command for each request")
    else:
        sub = f"Off {'on the charger' if active == 'charger' else 'on battery'}: the big model picks every command"
    rows.append(("computer-symbolic", "Small model", sub))
    big = str(settings.get("big_model"))
    if not ollama_status.get("running"):
        state = "Ollama is stopped, so the big model can't answer"
    else:
        loaded = any(m.get("name", "").split(":latest")[0] == big.split(":latest")[0]
                     for m in ollama_status.get("loaded") or [])
        if loaded:
            state = "Loaded now, answers right away"
        elif loading(speed)["big_model_policy"] == "instant":
            state = "Loading, it stays loaded"
        else:
            state = "Asleep: it loads in about 8 seconds when a request needs it"
    rows.append(("video-display-symbolic", f"Big model: {model_name(big)}",
                 f"{state}, on the {WHERE.get(settings.get('big_device'), 'NVIDIA GPU')}"))
    return rows


class RunningGroup(Adw.PreferencesGroup):
    def __init__(self, settings, status):
        super().__init__(title="Running Now", description="What answers you at this moment. It changes by "
                                                          "itself when you plug in or unplug the charger.")
        self.settings, self._status = settings, status
        self._rows: list[Adw.ActionRow] = []
        for _ in range(4):
            row = Adw.ActionRow(use_markup=False)
            icon = Gtk.Image(css_classes=["dim-label"])
            row.add_prefix(icon)
            row.icon = icon
            self._rows.append(row)
            self.add(row)
        self.sync()

    def sync(self) -> None:
        try:
            for row, (icon, title, sub) in zip(self._rows, running_rows(self.settings, self._status())):
                row.icon.set_from_icon_name(icon)
                row.set_title(title)
                row.set_subtitle(sub)
        except Exception:
            log.exception("running now")


class SideBySide(Adw.PreferencesGroup):
    """Battery | Charger columns: each is used while the laptop is in that state."""

    def __init__(self, settings):
        super().__init__(title="Battery and Charger",
                         description="Each column is used while the laptop is in that state. The models below "
                                     "are the same for both.")
        self.settings = settings
        self._cells: dict[tuple[str, str], Gtk.Widget] = {}
        self._heads: dict[str, Gtk.Label] = {}
        self._syncing = False
        self._handlers: list[tuple[object, int]] = []
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin_top=12, margin_bottom=12, margin_start=12,
                        margin_end=12, column_homogeneous=False)
        frame = Gtk.Frame(css_classes=["card"], child=grid)
        for col, profile in enumerate(PROFILES, start=1):
            head = Gtk.Label(label=NAMES[profile], xalign=0, hexpand=True, css_classes=["heading"])
            self._heads[profile] = head
            grid.attach(head, col, 0, 1, 1)
        for r, (key, title, hint, options) in enumerate(COLUMNS, start=1):
            label = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            label.append(Gtk.Label(label=title, xalign=0, wrap=True, width_chars=14, max_width_chars=24))
            label.append(Gtk.Label(label=hint, xalign=0, wrap=True, width_chars=14, max_width_chars=24,
                                   css_classes=["caption", "dim-label"]))
            grid.attach(label, 0, r, 1, 1)
            for col, profile in enumerate(PROFILES, start=1):
                cell = self._cell(profile, key, title, options)
                self._cells[(profile, key)] = cell
                grid.attach(cell, col, r, 1, 1)
                self._connect(settings, f"changed::{profile}.{key}", lambda *_: self.sync())
        self._speed_note = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"])
        grid.attach(self._speed_note, 0, len(COLUMNS) + 1, len(PROFILES) + 1, 1)
        self._connect(settings, "profile-changed", lambda *_: self.sync())
        self._connect(settings, "changed::profile", lambda *_: self.sync())
        self.add(frame)
        self.sync()

    def _connect(self, obj, signal: str, cb) -> None:
        self._handlers.append((obj, obj.connect(signal, cb)))

    def _cell(self, profile: str, key: str, title: str, options) -> Gtk.Widget:
        if options is None:
            sw = Gtk.Switch(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
            sw.connect("notify::active", lambda s, _p: self._set(profile, key, s.get_active()))
            sw.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}, {NAMES[profile].lower()}"])
            return sw
        dd = Gtk.DropDown.new_from_strings([label for _, label in options])
        dd.ids = [i for i, _ in options]
        dd.set_hexpand(True)
        dd.set_valign(Gtk.Align.CENTER)
        dd.update_property([Gtk.AccessibleProperty.LABEL], [f"{title}, {NAMES[profile].lower()}"])
        dd.connect("notify::selected", lambda d, _p: d.get_selected() < len(d.ids) and
                   self._set(profile, key, d.ids[d.get_selected()]))
        return dd

    def _set(self, profile: str, key: str, value) -> None:
        if self._syncing or self.settings.get_for(profile, key) == value:
            return
        try:
            self.settings.set_for(profile, key, value)
        except Exception:
            log.exception("saving %s.%s", profile, key)

    def sync(self) -> None:
        self._syncing = True
        try:
            active = self.settings.active_profile()
            for profile, head in self._heads.items():
                head.set_label(f"{NAMES[profile]} · in use" if profile == active else NAMES[profile])
            for (profile, key), cell in self._cells.items():
                value = self.settings.get_for(profile, key)
                if isinstance(cell, Gtk.Switch):
                    cell.set_active(bool(value))
                elif str(value) in cell.ids:
                    cell.set_selected(cell.ids.index(str(value)))
            speed = str(self.settings.get("speed"))
            text = next((t for i, _, t in SPEEDS if i == speed), "")
            self._speed_note.set_label(f"In use now, {SPEED_LABELS.get(speed, speed)}. {text}" if text else "")
        finally:
            self._syncing = False

    def close(self) -> None:
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
