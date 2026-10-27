"""Name test: the user reads a few sentences with the chosen name; the engine transcribes them and the app's wake
matcher scores how reliably the name is heard. Nothing said here is acted on."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux import wake  # noqa: E402

TIMEOUT_S = 15  # no speech by then counts as not heard
LEVEL_CSS = {"good": "success", "fair": "warning", "poor": "error"}


class NameTestDialog(Adw.Dialog):
    def __init__(self, ctx, name: str, on_use=None):
        self._name = name.strip().title()
        super().__init__(title=f"Test “{self._name}”", content_width=460)
        self._ctx, self._on_use = ctx, on_use
        self._run = wake.NameTest(name)
        self._timer = 0
        self._waiting = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=18,
                      margin_start=18, margin_end=18)
        box.append(Gtk.Label(wrap=True, xalign=0, css_classes=["dimmed"],
                             label="Press Start, then read the sentence aloud in your normal voice. "
                                   "Nothing you say here is carried out."))
        for warning in wake.warnings(name):
            box.append(Gtk.Label(label=warning, wrap=True, xalign=0, css_classes=["warning"]))
        self._sentence = Gtk.Label(wrap=True, xalign=0, css_classes=["title-3"])
        self._status = Gtk.Label(wrap=True, xalign=0, css_classes=["dimmed"])
        self._log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._verdict = Gtk.Label(wrap=True, xalign=0, visible=False, css_classes=["heading"])
        self._button = Gtk.Button(label="Start", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        self._button.connect("clicked", lambda *_: self._on_button())
        for widget in (self._sentence, self._status, self._log, self._verdict, self._button):
            box.append(widget)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        self.set_child(view)
        self.connect("closed", lambda *_: self._stop())
        self._show_current()

    def _show_current(self) -> None:
        n = len(self._run.results) + 1
        note = " This one has no name, so it should not wake." if self._run.on_negative else ""
        self._sentence.set_label(f"“{self._run.current}”")
        self._status.set_label(f"Sentence {n} of {len(self._run.sentences)}.{note}")

    def _on_button(self) -> None:
        if self._run.done:
            if self._run.verdict()[0] != "poor" and self._on_use is not None:
                self._on_use()
            self.close()
            return
        if not self._ctx.controller.name_test(self._heard):
            self._status.set_label("The speech engine isn't running. Start the assistant, then try again.")
            return
        self._waiting = True
        self._button.set_sensitive(False)
        self._status.set_label("Listening. Read it aloud now.")
        self._timer = GLib.timeout_add_seconds(TIMEOUT_S, self._timed_out)

    def _timed_out(self) -> bool:
        self._timer = 0
        self._ctx.controller.name_test(None)
        self._heard("")
        return False

    def _heard(self, text: str) -> None:
        if not self._waiting:
            return
        self._waiting = False
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        ok = self._run.record(text)
        said = f"Heard “{text}”" if text else "Heard nothing"
        self._log.append(Gtk.Label(label=("✓ " if ok else "✗ ") + said, xalign=0, wrap=True,
                                   css_classes=["success" if ok else "error"]))
        self._button.set_sensitive(True)
        if not self._run.done:
            self._button.set_label("Next")
            self._show_current()
            return
        level, advice = self._run.verdict()
        self._sentence.set_visible(False)
        self._status.set_visible(False)
        self._verdict.set_label(advice)
        self._verdict.add_css_class(LEVEL_CSS[level])
        self._verdict.set_visible(True)
        usable = level != "poor" and self._on_use is not None
        self._button.set_label(f"Use “{self._name}”" if usable else "Close")

    def _stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._waiting:
            self._waiting = False
            self._ctx.controller.name_test(None)
