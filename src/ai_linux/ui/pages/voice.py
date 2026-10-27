"""Voice page: which microphone, how the assistant listens, and how it sounds."""

from __future__ import annotations

import logging
import math
import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ai_linux import audio_meter  # noqa: E402
from ai_linux.config import ASSETS_DIR, ProfileView  # noqa: E402
from ai_linux.ui.profile_group import ProfileGroup  # noqa: E402

log = logging.getLogger(__name__)

SAMPLES_DIR = ASSETS_DIR / "samples"
TOGGLE_COMMAND = "ai-linux-app --toggle"

LISTEN_MODES = (
    ("wake", "Wake word"),
    ("hotkey", "Hotkey only"),
    ("always", "Always on"),
    ("off", "Off"),
)
FEEDBACK = (
    ("sound", "A chime"),
    ("speech", "Spoken"),
)
STT_ENGINES = (
    ("parakeet-tdt", "Parakeet TDT, accurate"),
    ("parakeet-ctc", "Parakeet CTC, fast"),
)
VOICES = (
    ("supertonic:M1", "Male 1", "SuperTonic M1, the default"),
    ("supertonic:M4", "Male 2", "SuperTonic M4"),
    ("supertonic:F1", "Female 1", "SuperTonic F1"),
    ("supertonic:F3", "Female 2", "SuperTonic F3"),
)
WAKE_SUGGESTIONS = ("Phoenix", "Computer", "Jarvis")
_LEVEL_HINT = "Speak to test. The microphone is used only while this page is open."
_WAKE_RE = re.compile(r"^[^\W\d_]+(?: [^\W\d_]+){0,2}$")  # one to three words, letters only


def _mode_hint(mode: str, wake_word: str) -> str:
    return {
        "wake": f"Say “{wake_word.strip().title()}”, then speak",
        "hotkey": "Press the shortcut, then speak",
        "always": "Every sentence is treated as a request",
        "off": "The microphone stays closed",
    }.get(mode, "")


class _Bindings:
    """Tracks signal handlers on long-lived objects so a destroyed page lets go of them."""

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


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Voice", tag="voice")
        self.ctx = ctx
        self.settings = ProfileView(ctx.settings)  # per-profile rows edit the profile picked on the page
        self._binds = _Bindings()
        self._syncing = False  # true while a row is updated from settings, so it does not write back
        self._meter = audio_meter.MicMeter(self.settings.get("mic_device") or None)
        self._meter_tick = 0
        self._level_bar: Gtk.LevelBar | None = None
        self._bars = None
        self._bars_state = "listening"
        self._sources: list[str] = [""]  # node names behind the device combo, "" = system default
        self._media: Gtk.MediaFile | None = None
        self._playing: str | None = None
        self._play_buttons: dict[str, Gtk.Button] = {}
        self._voice_checks: dict[str, Gtk.CheckButton] = {}
        self._volume_timer = 0

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._toasts = Adw.ToastOverlay()
        prefs = Adw.PreferencesPage()
        prefs.add(self._build_microphone())
        prefs.add(self._build_listening())
        self._profiles = ProfileGroup(self.settings)
        prefs.add(self._profiles)
        prefs.add(self._profiles.titled(self._build_how(), "Listening"))
        prefs.add(self._profiles.titled(self._build_voice(), "Replies"))
        self._toasts.set_child(prefs)
        view.set_content(self._toasts)
        self.set_child(view)

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)

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

    def _choice(self, key: str, title: str, options, use_subtitle: bool = False) -> Adw.ComboRow:
        ids = [o[0] for o in options]
        row = Adw.ComboRow(title=title, use_subtitle=use_subtitle, use_markup=False,
                           model=Gtk.StringList.new([o[1] for o in options]))
        current = self.settings.get(key)
        row.set_selected(ids.index(current) if current in ids else 0)
        self._watch(key, lambda: row.set_selected(ids.index(self.settings.get(key))
                                                  if self.settings.get(key) in ids else 0))
        row.ids = ids
        return row

    def _toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3))

    @staticmethod
    def _note(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0, wrap=True, css_classes=["dim-label", "caption", "ail-sp-note"])
        return label

    # microphone

    def _build_microphone(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Microphone")
        self._device_row = Adw.ComboRow(title="Input device", use_subtitle=True, use_markup=False,
                                        model=Gtk.StringList.new(["System default"]))
        self._device_row.add_prefix(Gtk.Image(icon_name="audio-input-microphone-symbolic"))
        self._set_device_model([], None)
        self._device_row.connect("notify::selected", self._on_device_selected)
        self._watch("mic_device", self._on_mic_setting)
        group.add(self._device_row)

        self._level_row = Adw.ActionRow(title="Input level", subtitle=_LEVEL_HINT)
        self._level_row.add_prefix(Gtk.Image(icon_name="microphone-sensitivity-high-symbolic"))
        self._level_row.add_suffix(self._make_bars())
        group.add(self._level_row)
        return group

    def _level(self) -> float:
        if self.ctx.screenshot_mode:
            # screenshot mode never opens the real microphone
            return 0.55 + 0.25 * math.sin(time.monotonic() * 6.0)
        return self._meter.level

    def _make_bars(self) -> Gtk.Widget:
        try:
            from ai_linux.ui.bars import WaveBars

            bars = WaveBars(None, "pill")
            bars.set_level_source(self._level)
            bars.set_state("listening")
            bars.set_valign(Gtk.Align.CENTER)
            self._bars = bars
            return bars
        except Exception:
            log.info("WaveBars unavailable, using a level bar", exc_info=True)
        self._bars = None
        self._level_bar = Gtk.LevelBar(min_value=0.0, max_value=1.0, valign=Gtk.Align.CENTER,
                                       width_request=140, css_classes=["ail-sp-level"])
        self._level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self._level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH)
        self._level_bar.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_FULL)
        return self._level_bar

    def _set_device_model(self, sources: list[tuple[str, str]], default: str | None) -> None:
        names = {n: d for n, d in sources}
        label = "System default"
        if default and default in names:
            label = f"System default ({names[default]})"
        items, self._sources = [label], [""]
        for name, desc in sources:
            items.append(desc)
            self._sources.append(name)
        chosen = self.settings.get("mic_device") or ""
        if chosen and chosen not in self._sources:
            items.append(f"{chosen} (not connected)")
            self._sources.append(chosen)
        self._syncing = True
        try:
            self._device_row.set_model(Gtk.StringList.new(items))
            self._device_row.set_selected(self._sources.index(chosen) if chosen in self._sources else 0)
        finally:
            self._syncing = False

    def _on_device_selected(self, row, _pspec) -> None:
        if self._syncing:
            return
        i = row.get_selected()
        name = self._sources[i] if 0 <= i < len(self._sources) else ""
        if name:
            self._store("mic_device", name)
        elif self.settings.is_set("mic_device"):
            self.settings.reset("mic_device")  # back to default: forget the choice entirely
        self._meter.set_device(name or None)

    def _on_mic_setting(self) -> None:
        chosen = self.settings.get("mic_device") or ""
        if chosen in self._sources:
            self._device_row.set_selected(self._sources.index(chosen))
        self._meter.set_device(chosen or None)

    def _on_sources(self, sources, default) -> None:
        try:
            self._set_device_model(sources, default)
            if not sources:
                self._level_row.set_subtitle("No microphone found")
        except Exception:
            log.exception("updating microphone list")

    def _meter_poll(self) -> bool:
        if self._level_bar is not None:
            target = self._level()
            cur = self._level_bar.get_value()
            self._level_bar.set_value(target if target > cur else cur * 0.82 + target * 0.18)
        error = None if self.ctx.screenshot_mode else self._meter.error
        if error:
            self._level_row.set_subtitle(GLib.markup_escape_text(f"Microphone unavailable: {error}"))
            self._level_row.add_css_class("ail-sp-warning")
        else:
            self._level_row.set_subtitle(_LEVEL_HINT)
            self._level_row.remove_css_class("ail-sp-warning")
        bars_state = "muted" if error else "listening"
        if self._bars is not None and bars_state != self._bars_state:
            self._bars_state = bars_state
            try:
                self._bars.set_state(bars_state)
            except Exception:
                log.debug("bars state", exc_info=True)
        return True

    def _on_map(self, *_):
        audio_meter.list_sources_async(self._on_sources)
        if not self.ctx.screenshot_mode:
            self._meter.start()
        if not self._meter_tick:
            self._meter_tick = GLib.timeout_add(33 if self._level_bar is not None else 250, self._meter_poll)
        self._meter_poll()

    def _on_unmap(self, *_):
        self._meter.stop()
        if self._meter_tick:
            GLib.source_remove(self._meter_tick)
            self._meter_tick = 0
        self._stop_preview()

    def _on_destroy(self, *_):
        self._on_unmap()
        self._binds.clear()
        self._profiles.close()
        self.settings.close()

    # listening

    def _build_listening(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Wake Word and Shortcut",
                                     description="The wake word is also the assistant's name.")
        group.add(self._build_wake_row())
        for row in self._build_hotkey_rows():
            group.add(row)
        group.add(self._switch("hold_to_talk", "Hold to talk", "Hold the shortcut while you speak, release to send"))
        group.add(self._switch("start_ping", "Start sound", "A short chime when the assistant starts listening"))
        group.add(self._switch("barge_in", "Talk over replies", "Echo cancellation through PipeWire"))
        return group

    def _build_how(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Listening")
        mode_row = self._choice("listen_mode", "Listen for", LISTEN_MODES)

        def update_hint():
            hint = _mode_hint(self.settings.get("listen_mode"), str(self.settings.get("wake_word")))
            mode_row.set_subtitle(hint)  # the row has use_markup off
            filter_row.set_sensitive(self.settings.get("listen_mode") == "always")

        def on_mode(row, _p):
            if self._syncing:
                return
            mode = row.ids[row.get_selected()]
            if mode != self.settings.get("listen_mode"):
                try:
                    if self.settings.profile == self.settings.active_profile():
                        self.ctx.controller.set_listen_mode(mode)  # applies now
                    else:
                        self._store("listen_mode", mode)
                except Exception:
                    log.exception("set_listen_mode(%s)", mode)
            update_hint()

        filter_row = self._switch("addressed_check", "Ignore other talk",
                                  "Always on only: the small model skips speech that isn't meant for the assistant, "
                                  "such as a call or the TV")
        mode_row.connect("notify::selected", on_mode)
        self._watch("listen_mode", update_hint)
        self._watch("wake_word", update_hint)
        update_hint()
        group.add(mode_row)
        group.add(filter_row)

        stt = self._choice("stt_engine", "Speech recognition", STT_ENGINES, use_subtitle=True)
        stt.connect("notify::selected", lambda r, _p: self._store("stt_engine", r.ids[r.get_selected()]))
        group.add(stt)
        return group

    def _build_wake_row(self) -> Adw.EntryRow:
        row = Adw.EntryRow(title="Wake word", show_apply_button=True)
        row.set_text(self.settings.get("wake_word").title())

        def apply(*_):
            word = " ".join(row.get_text().split())
            if not _WAKE_RE.match(word):
                row.add_css_class("error")
                self._toast("Use one to three words, letters only")
                return
            row.remove_css_class("error")
            self._store("wake_word", word.lower())

        row.connect("apply", apply)
        row.connect("changed", lambda r: r.remove_css_class("error"))

        menu = Gio.Menu()
        for word in WAKE_SUGGESTIONS:
            menu.append(word, f"wake.pick::{word}")
        group = Gio.SimpleActionGroup()
        pick = Gio.SimpleAction.new("pick", GLib.VariantType.new("s"))

        def on_pick(_a, value):
            row.set_text(value.get_string())
            apply()

        pick.connect("activate", on_pick)
        group.add_action(pick)
        row.insert_action_group("wake", group)
        button = Gtk.MenuButton(icon_name="pan-down-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                                tooltip_text="Suggestions", css_classes=["flat"])
        row.add_suffix(button)
        self._watch("wake_word", lambda: row.set_text(self.settings.get("wake_word").title()))
        return row

    def _build_hotkey_rows(self) -> list[Adw.PreferencesRow]:
        row = Adw.ActionRow(title="Shortcut")
        accel = self.settings.get("hotkey") or ""
        ok, _key, _mods = Gtk.accelerator_parse(accel)
        if ok:
            row.add_suffix(Adw.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
        else:
            row.add_suffix(Gtk.Label(label=accel or "Not set", css_classes=["dim-label"]))
        hotkey = self.ctx.hotkey
        available = bool(hotkey is not None and getattr(hotkey, "available", False))
        desc = getattr(hotkey, "trigger_description", None) if available else None
        row.set_subtitle(desc or "Starts listening from any app")
        if available:
            return [row]
        # no GlobalShortcuts portal: a GNOME custom shortcut running the toggle command does the same
        shown = TOGGLE_COMMAND.replace("-", "\u2011")  # non-breaking hyphens keep the command on one line
        setup = Adw.ActionRow(
            title="Add a GNOME shortcut", activatable=True, tooltip_text="Open keyboard settings",
            subtitle=f"The shortcut service is unavailable. In Settings, Keyboard, add a custom shortcut "
                     f"that runs <tt>{shown}</tt>",
        )
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Copy the command", css_classes=["flat"])
        copy.connect("clicked", self._copy_toggle_command)
        setup.add_suffix(copy)
        setup.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        setup.connect("activated", self._open_keyboard_settings)
        return [row, setup]

    def _copy_toggle_command(self, button) -> None:
        try:
            button.get_clipboard().set(TOGGLE_COMMAND)
            self._toast("Command copied")
        except Exception:
            log.exception("copy to clipboard")

    def _open_keyboard_settings(self, _button) -> None:
        try:
            GLib.spawn_async(["gnome-control-center", "keyboard"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error as exc:
            log.warning("opening keyboard settings: %s", exc.message)
            self._toast("Could not open Settings")

    # voice output

    def _build_voice(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Replies")
        group.add(self._switch("tts_enabled", "Speak replies",
                               "Off: replies and chimes only show on screen"))
        feedback = self._choice("action_feedback", "Finished actions", FEEDBACK)
        feedback.set_subtitle("Failures and answers are always spoken")
        feedback.connect("notify::selected", lambda r, _p: self._store("action_feedback", r.ids[r.get_selected()]))
        group.add(feedback)
        group.add(self._switch("big_cue", "Big model chime",
                               "A sound when the big model takes over, so you know the answer takes a moment"))
        current = self.settings.get("voice")
        leader: Gtk.CheckButton | None = None
        for vid, title, subtitle in VOICES:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=(vid == current))
            if leader is None:
                leader = check
            else:
                check.set_group(leader)
            check.connect("toggled", lambda c, v=vid: c.get_active() and self._store("voice", v))
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable_widget=check)
            row.add_prefix(check)
            play = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=f"Play a sample of {title}", css_classes=["flat", "circular"])
            play.connect("clicked", lambda _b, v=vid: self._toggle_preview(v))
            row.add_suffix(play)
            self._play_buttons[vid] = play
            self._voice_checks[vid] = check
            group.add(row)

        # a voice set elsewhere (the set_voice tool) gets its own row so the choice stays visible
        self._other_check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=leader)
        self._other_row = Adw.ActionRow(title="Other voice", activatable_widget=self._other_check)
        self._other_row.add_prefix(self._other_check)
        group.add(self._other_row)
        self._sync_voice()
        self._watch("voice", self._sync_voice)

        speed = Adw.SpinRow.new_with_range(0.8, 1.3, 0.05)
        speed.set_title("Speaking speed")
        speed.set_subtitle("1.00 is normal")
        speed.set_digits(2)
        speed.set_value(float(self.settings.get("tts_speed")))
        speed.connect("notify::value", lambda r, _p: self._store("tts_speed", round(r.get_value(), 2)))
        self._watch("tts_speed", lambda: speed.set_value(float(self.settings.get("tts_speed"))))
        group.add(speed)

        group.add(self._build_volume_row())
        group.add(self._note("Voice changes apply to the next reply."))
        return group

    def _sync_voice(self) -> None:
        current = self.settings.get("voice")
        known = current in self._voice_checks
        self._other_row.set_visible(not known)
        if known:
            self._voice_checks[current].set_active(True)
        else:
            self._other_row.set_subtitle(GLib.markup_escape_text(str(current)))
            self._other_check.set_active(True)

    def _build_volume_row(self) -> Adw.ActionRow:
        row = Adw.ActionRow(title="Volume")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(160, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.set_value(float(self.settings.get("tts_volume")))
        scale.update_property([Gtk.AccessibleProperty.LABEL], ["Voice volume"])

        def on_value(s):
            if self._syncing:
                return
            s.set_tooltip_text(f"{round(s.get_value() * 100)}%")
            if self._volume_timer:
                GLib.source_remove(self._volume_timer)
            self._volume_timer = GLib.timeout_add(250, commit)  # one write per drag, not per pixel
            if self._media is not None:
                self._media.set_volume(s.get_value())

        def commit():
            self._volume_timer = 0
            self._store("tts_volume", round(scale.get_value(), 2))
            return False

        scale.connect("value-changed", on_value)
        scale.set_tooltip_text(f"{round(scale.get_value() * 100)}%")
        self._watch("tts_volume", lambda: scale.set_value(float(self.settings.get("tts_volume"))))
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        box.append(Gtk.Image(icon_name="audio-volume-low-symbolic", css_classes=["dim-label"]))
        box.append(scale)
        box.append(Gtk.Image(icon_name="audio-volume-high-symbolic", css_classes=["dim-label"]))
        row.add_suffix(box)
        return row

    # previews

    def _toggle_preview(self, vid: str) -> None:
        if self._playing == vid:
            self._stop_preview()
            return
        self._stop_preview()
        path = SAMPLES_DIR / f"{vid.split(':')[-1]}.ogg"
        if not path.is_file():
            self._toast("No sample for this voice")
            return
        try:
            media = Gtk.MediaFile.new_for_filename(str(path))
            media.set_volume(float(self.settings.get("tts_volume")))
            media.connect("notify::ended", lambda m, _p: m.get_ended() and self._stop_preview())
            media.connect("notify::error", self._on_media_error)
            media.play()
        except Exception:
            log.exception("voice preview %s", vid)
            self._toast("Could not play the sample")
            return
        self._media, self._playing = media, vid
        btn = self._play_buttons[vid]
        btn.set_icon_name("media-playback-stop-symbolic")
        btn.set_tooltip_text("Stop the sample")

    def _on_media_error(self, media, _pspec) -> None:
        err = media.get_error()
        if err is not None:
            log.warning("voice preview failed: %s", err.message)
            self._stop_preview()
            self._toast("Could not play the sample")

    def _stop_preview(self) -> None:
        media, vid = self._media, self._playing
        self._media, self._playing = None, None
        if media is not None:
            try:
                media.pause()
                media.clear()
            except Exception:
                log.debug("stopping preview", exc_info=True)
        if vid in self._play_buttons:
            btn = self._play_buttons[vid]
            btn.set_icon_name("media-playback-start-symbolic")
            btn.set_tooltip_text("Play a sample")


__all__ = ["Page"]
