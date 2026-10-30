"""Welcome tour: six short steps shown once on first launch, and again from Preferences."""

from __future__ import annotations

import logging
import math
import re
import time
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux import models_catalog  # noqa: E402
from ai_linux.config import ICON_NAME  # noqa: E402

log = logging.getLogger(__name__)

STEPS = ("welcome", "microphone", "models", "listening", "try", "done")
LISTEN_MODES = (
    ("wake", "Wake word"),
    ("hotkey", "Hotkey only"),
    ("always", "Always on"),
    ("off", "Off"),
)
_WAKE_RE = re.compile(r"^[^\W\d_]+(?: [^\W\d_]+){0,2}$")  # same rule as the Voice page
_METER_POLL_MS = 250
_current: weakref.ref | None = None


def maybe_show(parent, ctx):
    """Show the tour unless it was finished or skipped before; returns the dialog or None."""
    if ctx.settings.get("onboarding_done"):
        return None
    return show(parent, ctx)


def show(parent, ctx):
    """Show the tour now (one at a time) and return the dialog."""
    global _current
    existing = _current() if _current is not None else None
    if existing is not None and not existing.closed:
        return existing
    dialog = Onboarding(ctx)
    _current = weakref.ref(dialog)
    dialog.present(parent)
    return dialog


def _label(text: str, *classes: str, wrap: bool = True) -> Gtk.Label:
    return Gtk.Label(label=text, wrap=wrap, justify=Gtk.Justification.CENTER, css_classes=list(classes),
                     max_width_chars=44)


def _bars(bus, state: str, level_fn=None) -> Gtk.Widget:
    try:
        from ai_linux.ui.bars import WaveBars

        bars = WaveBars(bus, "hero")
        if bus is None:
            bars.set_state(state)
        if level_fn is not None:
            bars.set_level_source(level_fn)
        bars.set_halign(Gtk.Align.CENTER)
        bars.set_valign(Gtk.Align.CENTER)
        return bars
    except Exception:
        log.exception("tour: bars unavailable")
        return Gtk.Image(icon_name="audio-input-microphone-symbolic", pixel_size=64)


class Onboarding(Adw.Dialog):
    """``go_to(index)`` jumps to a step; closing at any point counts as done."""

    __gtype_name__ = "AilOnboarding"

    def __init__(self, ctx):
        super().__init__(title="Welcome", content_width=560, content_height=620, can_close=True)
        self.ctx = ctx
        self.settings = ctx.settings
        self.closed = False
        self._index = 0
        self._meter = None
        self._meter_id = 0
        self._handlers: list[tuple[object, int]] = []
        self._mic_bars = None
        self._mic_state = "listening"
        self._syncing = False

        self._carousel = Adw.Carousel(vexpand=True, allow_scroll_wheel=False, allow_long_swipes=False)
        self._carousel.connect("page-changed", lambda _c, index: self._on_page(index))
        for build in (self._welcome, self._microphone, self._models, self._listening, self._try, self._done):
            self._carousel.append(build())

        header = Adw.HeaderBar(show_title=False, show_end_title_buttons=False)
        self._skip = Gtk.Button(label="Skip", css_classes=["flat"], tooltip_text="Close the tour")
        self._skip.connect("clicked", lambda *_: self.close())
        header.pack_end(self._skip)

        self._back = Gtk.Button(label="Back", valign=Gtk.Align.CENTER, css_classes=["pill"])
        self._back.connect("clicked", lambda *_: self.go_to(self._index - 1))
        self._next = Gtk.Button(label="Next", valign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        self._next.connect("clicked", lambda *_: self._advance())
        dots = Adw.CarouselIndicatorDots(carousel=self._carousel, valign=Gtk.Align.CENTER)
        footer = Gtk.CenterBox(css_classes=["ail-tour-footer"])
        footer.set_start_widget(self._back)
        footer.set_center_widget(dots)
        footer.set_end_widget(self._next)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._carousel)
        view.add_bottom_bar(footer)
        self.set_child(view)
        self.set_default_widget(self._next)
        self.set_focus(self._next)

        self.connect("closed", self._on_closed)
        self.connect("map", lambda *_: self._sync_meter())
        self.connect("unmap", lambda *_: self._stop_meter())
        self._on_page(0)

    # navigation

    def go_to(self, index: int, animate: bool = True) -> None:
        index = max(0, min(len(STEPS) - 1, int(index)))
        self._carousel.scroll_to(self._carousel.get_nth_page(index), animate)
        self._on_page(index)

    def _advance(self) -> None:
        if self._index >= len(STEPS) - 1:
            self.close()
        else:
            self.go_to(self._index + 1)

    def _on_page(self, index: int) -> None:
        index = int(index)
        self._index = index
        last = index == len(STEPS) - 1
        self._back.set_visible(index > 0)
        self._skip.set_visible(not last)
        self._next.set_label("Get Started" if index == 0 else "Done" if last else "Next")
        if last:
            self._finish()
        self._sync_meter()

    def _finish(self) -> None:
        try:
            self.settings.set("onboarding_done", True)
        except Exception:
            log.exception("tour: saving onboarding_done")

    def _on_closed(self, *_args) -> None:
        self.closed = True
        self._stop_meter()
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
        self._finish()  # skipping counts too, the tour stays reachable from Preferences

    def _watch(self, key: str, fn) -> None:
        self._handlers.append((self.settings, self.settings.connect(f"changed::{key}", lambda *_: fn())))

    # layout helpers

    def _step(self, hero: Gtk.Widget | None, title: str, body: str, *extra: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                      hexpand=True, css_classes=["ail-tour-step"])
        if hero is not None:
            holder = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["ail-tour-hero"])
            hero.set_valign(Gtk.Align.CENTER)
            holder.append(hero)
            box.append(holder)
        box.append(_label(title, "ail-tour-title"))
        if body:
            box.append(_label(body, "ail-tour-body"))
        for widget in extra:
            box.append(widget)
        clamp = Adw.Clamp(maximum_size=460, child=box)
        # scrolls only when large text or a short screen would otherwise clip the step
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, hexpand=True, vexpand=True)

    @staticmethod
    def _icon(name: str) -> Gtk.Widget:
        return Gtk.Image(icon_name=name, pixel_size=44, halign=Gtk.Align.CENTER, css_classes=["ail-tour-icon"])

    # steps

    def _welcome(self) -> Gtk.Widget:
        chip = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, css_classes=["ail-tour-chip"])
        chip.append(Gtk.Image(icon_name="channel-secure-symbolic"))
        chip.append(Gtk.Label(label="Runs on this computer. Nothing you say leaves it."))
        chip.set_margin_top(10)
        icon = Gtk.Image(icon_name=ICON_NAME, pixel_size=128, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         css_classes=["icon-dropshadow"])  # the phoenix itself greets; bars come with the mic step
        return self._step(
            icon, "Welcome to Phoenix",
            "Talk to your desktop. Open apps, change settings and get quick answers, all by voice.", chip)

    def _microphone(self) -> Gtk.Widget:
        self._mic_bars = _bars(None, "listening", self._mic_level)
        device = self.settings.get("mic_device")
        self._mic_hint = (f"Using {device}." if device else "Using the system default microphone.") + \
            " You can pick another on the Voice page."
        self._mic_note = _label(self._mic_hint, "dim-label", "caption")
        self._mic_note.set_margin_top(6)
        return self._step(self._mic_bars, "Check your microphone",
                          "Say a few words. The bars move when it hears you.", self._mic_note)

    def _models(self) -> Gtk.Widget:
        cards = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list", "ail-tour-cards"])
        cards.set_margin_top(10)
        for slot, role in (("speech", "Hears what you say"), ("small", "Everyday commands on battery"),
                           ("big", "Questions, chat and the screen")):
            spec = next((m for m in models_catalog.for_slot(slot) if m.recommended), None)
            if spec is None:
                continue
            where = ("runs on the Intel GPU or CPU" if spec.runtime == "onnx" or not spec.vram_gb
                     else f"needs about {spec.vram_gb:g} GB of GPU memory")
            row = Adw.ActionRow(title=spec.name, subtitle=f"{role} · {spec.download_gb:g} GB download · {where}",
                                use_markup=False, subtitle_lines=2)
            icon = {"speech": "audio-input-microphone-symbolic", "small": "power-profile-balanced-symbolic"}.get(
                slot, "power-profile-performance-symbolic")
            row.add_prefix(Gtk.Image(icon_name=icon, css_classes=["ail-sp-icon"], valign=Gtk.Align.CENTER))
            cards.append(row)
        open_models = Adw.ButtonRow(title="Download now or later from the Models page",
                                    end_icon_name="go-next-symbolic")
        open_models.connect("activated", lambda *_: self._open_models())
        cards.append(open_models)
        return self._step(
            self._icon("folder-download-symbolic"), "The models it needs",
            "Speech recognition runs on the Intel GPU. On battery a small model picks everyday commands; a bigger one "
            "on the graphics card answers questions. Nothing downloads until you ask.", cards)

    def _listening(self) -> Gtk.Widget:
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        group.set_margin_top(10)

        mode_row = Adw.ComboRow(title="Listen for", model=Gtk.StringList.new([t for _, t in LISTEN_MODES]),
                                use_subtitle=False)
        ids = [m for m, _ in LISTEN_MODES]

        def sync_mode():
            mode = self.settings.get("listen_mode")
            self._syncing = True
            mode_row.set_selected(ids.index(mode) if mode in ids else 0)
            self._syncing = False
            wake_row.set_sensitive(mode == "wake")

        def on_mode(row, _pspec):
            if self._syncing:
                return
            mode = ids[row.get_selected()]
            if mode != self.settings.get("listen_mode"):
                try:
                    self.ctx.controller.set_listen_mode(mode)
                except Exception:
                    log.exception("tour: set_listen_mode(%s)", mode)
            wake_row.set_sensitive(mode == "wake")

        wake_row = Adw.EntryRow(title="Wake word", show_apply_button=True)
        wake_row.set_text(str(self.settings.get("wake_word")).title())

        def apply(*_):
            word = " ".join(wake_row.get_text().split())
            if not _WAKE_RE.match(word):
                wake_row.add_css_class("error")
                return
            wake_row.remove_css_class("error")
            try:
                self.settings.set("wake_word", word.lower())
            except Exception:
                log.exception("tour: saving wake word")

        wake_row.connect("apply", apply)
        wake_row.connect("changed", lambda r: r.remove_css_class("error"))
        mode_row.connect("notify::selected", on_mode)

        hotkey_row = Adw.ActionRow(title="Shortcut", subtitle="Press it anywhere to start talking")
        self._shortcut = None
        if hasattr(Adw, "ShortcutLabel"):
            self._shortcut = Adw.ShortcutLabel(accelerator=self.settings.get("hotkey") or "",
                                               valign=Gtk.Align.CENTER)
            hotkey_row.add_suffix(self._shortcut)
        else:
            hotkey_row.set_subtitle(f"Press {self._hotkey_text()} anywhere to start talking")

        for row in (mode_row, wake_row, hotkey_row):
            group.append(row)
        sync_mode()
        self._watch("listen_mode", sync_mode)
        self._watch("wake_word", lambda: wake_row.set_text(str(self.settings.get("wake_word")).title()))
        self._watch("hotkey", self._sync_hotkey)
        return self._step(self._icon("audio-input-microphone-symbolic"), "How it listens",
                          "Pick how you start a request. You can change this any time on the Voice page.",
                          group)

    def _try(self) -> Gtk.Widget:
        self._try_quote = _label("", "ail-tour-quote")
        self._try_hint = _label("", "ail-tour-body")
        self._sync_try()
        for key in ("listen_mode", "wake_word", "hotkey"):
            self._watch(key, self._sync_try)
        return self._step(_bars(self.ctx.bus, "idle"), "Try it", "", self._try_quote, self._try_hint)

    def _done(self) -> Gtk.Widget:
        tips = _label("Find past requests in History, and change anything later in Preferences.",
                      "dim-label", "caption")
        tips.set_margin_top(6)
        return self._step(self._icon("object-select-symbolic"), "You are all set",
                          "Phoenix keeps running in the background, ready when you are.", tips)

    # step details

    def _hotkey_text(self) -> str:
        desc = getattr(getattr(self.ctx, "hotkey", None), "trigger_description", None)
        if desc:
            return desc
        try:
            from ai_linux.hotkey import accel_label

            return accel_label(self.settings.get("hotkey")) or "the shortcut"
        except Exception:
            return "the shortcut"

    def _sync_hotkey(self) -> None:
        if self._shortcut is not None:
            self._shortcut.set_accelerator(self.settings.get("hotkey") or "")

    def _sync_try(self) -> None:
        mode = self.settings.get("listen_mode")
        word = str(self.settings.get("wake_word")).strip().title() or "Phoenix"
        if mode == "wake":
            quote, hint = f"Say “{word}, open Files”", "The bars light up while it listens and answers."
        elif mode == "hotkey":
            quote, hint = "Say “Open Files”", f"Press {self._hotkey_text()} first, then speak."
        elif mode == "always":
            quote, hint = "Say “Open Files”", "It is always listening, so just speak."
        else:
            quote, hint = "Say “Open Files”", "Listening is off. Turn it on in the previous step first."
        self._try_quote.set_label(quote)
        self._try_hint.set_label(hint)

    def _open_models(self) -> None:
        parent = self.get_root()
        win = getattr(self.ctx.app, "window", None) or parent
        self.close()
        show_page = getattr(win, "show_page", None)
        if show_page is None:
            log.info("tour: no main window to show the Models page")
            return
        try:
            show_page("models")
            win.present()
        except Exception:
            log.exception("tour: opening the Models page")

    # microphone

    def _mic_level(self) -> float:
        if self.ctx.screenshot_mode:
            # screenshot mode never opens the real microphone
            return 0.5 + 0.25 * math.sin(time.monotonic() * 6.0)
        meter = self._meter
        return meter.level if meter is not None else 0.0

    def _sync_meter(self) -> None:
        want = not self.closed and self._index == STEPS.index("microphone") and self.get_mapped()
        if want:
            self._start_meter()
        else:
            self._stop_meter()

    def _start_meter(self) -> None:
        if not self.ctx.screenshot_mode:
            try:
                if self._meter is None:
                    from ai_linux.audio_meter import MicMeter

                    self._meter = MicMeter(self.settings.get("mic_device") or None)
                self._meter.start()
            except Exception:
                log.exception("tour: microphone meter")
        if not self._meter_id:
            self._meter_id = GLib.timeout_add(_METER_POLL_MS, self._poll_meter)
        self._poll_meter()

    def _stop_meter(self) -> None:
        if self._meter is not None:
            self._meter.stop()  # ends pw-record so the microphone is free again
        if self._meter_id:
            GLib.source_remove(self._meter_id)
            self._meter_id = 0

    def _poll_meter(self) -> bool:
        error = None if self.ctx.screenshot_mode or self._meter is None else self._meter.error
        state = "muted" if error else "listening"
        if error:
            self._mic_note.set_label(f"Microphone unavailable: {error}")
            self._mic_note.add_css_class("error")
        elif self._mic_note.has_css_class("error"):
            self._mic_note.set_label(self._mic_hint)
            self._mic_note.remove_css_class("error")
        if state != self._mic_state and hasattr(self._mic_bars, "set_state"):
            self._mic_state = state
            self._mic_bars.set_state(state)
        return GLib.SOURCE_CONTINUE


__all__ = ["maybe_show", "show", "Onboarding", "STEPS"]
