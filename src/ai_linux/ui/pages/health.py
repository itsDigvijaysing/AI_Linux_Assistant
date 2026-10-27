"""Health page: what the assistant needs, whether this computer has it, and one-click fixes."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ai_linux import autostart, doctor  # noqa: E402
from ai_linux.config import DEFAULTS, LOG_DIR  # noqa: E402

log = logging.getLogger(__name__)

ICONS = {"ok": "object-select-symbolic", "warn": "dialog-warning-symbolic", "fail": "window-close-symbolic",
         "info": "dialog-information-symbolic"}
FIX_LABELS = {"enable_autostart": "Turn On", "start_ollama": "Start", "open_models": "Get Models"}
SUMMARY = {
    "ok": ("Everything is ready", "object-select-symbolic"),
    "warn": ("{n} need attention", "dialog-warning-symbolic"),
    "fail": ("{n} need attention", "dialog-error-symbolic"),
}
_SETTINGS_SNAPSHOT = ("ollama_url", "small_model", "big_model", "autostart")


def _plural(n: int) -> str:
    return "1 thing" if n == 1 else f"{n} things"


def _tilde(path) -> str:
    from pathlib import Path

    s, home = str(path), str(Path.home())
    return "~" + s[len(home):] if s.startswith(home + "/") else s


class _Snapshot:
    """Settings copied on the main thread so the worker never touches the GObject."""

    def __init__(self, settings):
        self._values = {}
        self._set = set()
        for key in _SETTINGS_SNAPSHOT:
            try:
                self._values[key] = settings.get(key)
                if settings.is_set(key):
                    self._set.add(key)
            except Exception:
                self._values[key] = DEFAULTS.get(key)

    def get(self, key):
        return self._values.get(key, DEFAULTS.get(key))

    def is_set(self, key) -> bool:
        return key in self._set


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Health", tag="health")
        self.ctx = ctx
        self._checks: list[doctor.Check] = []
        self._running = False
        self._generation = 0
        self._checked_at: float | None = None
        self._groups: list[Adw.PreferencesGroup] = []
        self._ollama_handler = 0
        self._recheck_id = 0

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check Again")
        self._refresh_btn.connect("clicked", lambda *_: self.refresh())
        header.pack_end(self._refresh_btn)
        view.add_top_bar(header)

        self._toasts = Adw.ToastOverlay()
        self._prefs = Adw.PreferencesPage()
        self._prefs.add_css_class("ail-health")
        self._prefs.add(self._build_summary())
        self._help_group = self._build_help()
        self._prefs.add(self._help_group)
        self._toasts.set_child(self._prefs)
        view.set_content(self._toasts)
        self.set_child(view)

        self.connect("map", lambda *_: self.refresh())
        self.connect("destroy", self._on_destroy)
        self._watch_ollama()

    # summary card

    def _build_summary(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        card = Gtk.Box(spacing=16, css_classes=["card", "ail-health-summary"])
        self._summary_badge = Gtk.Stack(valign=Gtk.Align.CENTER, css_classes=["ail-health-hero"])
        self._summary_icon = Gtk.Image(icon_name="object-select-symbolic", pixel_size=28)
        self._summary_badge.add_named(self._summary_icon, "icon")
        self._summary_badge.add_named(Adw.Spinner(width_request=28, height_request=28), "spinner")
        self._summary_badge.set_visible_child_name("spinner")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        self._summary_title = Gtk.Label(label="Checking your system…", xalign=0, wrap=True,
                                        css_classes=["title-3"])
        self._summary_sub = Gtk.Label(label="This takes a second", xalign=0, wrap=True,
                                      css_classes=["dim-label"])
        texts.append(self._summary_title)
        texts.append(self._summary_sub)
        card.append(self._summary_badge)
        card.append(texts)
        group.add(card)
        return group

    def _update_summary(self) -> None:
        badge = self._summary_badge
        for s in ("ok", "warn", "fail"):
            badge.remove_css_class(s)
        if self._running and not self._checks:
            badge.set_visible_child_name("spinner")
            self._summary_title.set_label("Checking your system…")
            self._summary_sub.set_label("This takes a second")
            return
        worst, attention = doctor.summarize(self._checks)
        title, icon = SUMMARY[worst]
        badge.add_css_class(worst)
        badge.set_visible_child_name("spinner" if self._running else "icon")
        self._summary_icon.set_from_icon_name(icon)
        self._summary_title.set_label(title.format(n=_plural(attention)))
        if self._running:
            sub = "Checking again…"
        elif worst == "ok":
            sub = "The assistant has everything it needs on this computer."
        else:
            sub = "Use the buttons below to fix what you can."
        if self._checked_at and not self._running:
            sub += f" Checked at {time.strftime('%H:%M', time.localtime(self._checked_at))}."
        self._summary_sub.set_label(sub)

    # help group

    def _build_help(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Get Help")
        report = Adw.ActionRow(title="Diagnostic report",
                               subtitle="Copy these results to share when asking for help. "
                                        "Leaves out your settings and personal folders.")
        report.add_prefix(Gtk.Image(icon_name="edit-copy-symbolic"))
        self._copy_btn = Gtk.Button(label="Copy", valign=Gtk.Align.CENTER, sensitive=False)
        self._copy_btn.connect("clicked", self._on_copy)
        report.add_suffix(self._copy_btn)
        report.set_activatable_widget(self._copy_btn)
        group.add(report)

        logs = Adw.ActionRow(title="Logs folder", subtitle=_tilde(LOG_DIR), subtitle_selectable=True)
        logs.add_prefix(Gtk.Image(icon_name="folder-open-symbolic"))
        open_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        open_btn.connect("clicked", self._on_open_logs)
        logs.add_suffix(open_btn)
        logs.set_activatable_widget(open_btn)
        group.add(logs)
        return group

    # running checks

    def refresh(self) -> None:
        if self._running:
            return
        self._running = True
        self._generation += 1
        gen = self._generation
        self._refresh_btn.set_sensitive(False)
        self._update_summary()
        ctx_like = SimpleNamespace(settings=_Snapshot(self.ctx.settings), ollama=getattr(self.ctx, "ollama", None),
                                   controller=getattr(self.ctx, "controller", None),
                                   pill=getattr(self.ctx, "pill", None))

        def work():
            try:
                checks = doctor.run_checks(ctx_like)
            except Exception:
                log.exception("health checks failed")
                checks = [doctor.Check("doctor", "Health checks", "fail", "The checks themselves failed. "
                                       "See the logs folder.", group="system")]
            GLib.idle_add(self._on_results, gen, checks)

        threading.Thread(target=work, name="health-checks", daemon=True).start()

    def _on_results(self, gen: int, checks: list) -> bool:
        if gen != self._generation:
            return GLib.SOURCE_REMOVE
        self._running = False
        self._checks = checks
        self._checked_at = time.time()
        try:
            self._render()
        except Exception:
            log.exception("rendering health results")
        self._refresh_btn.set_sensitive(True)
        self._copy_btn.set_sensitive(True)
        self._update_summary()
        return GLib.SOURCE_REMOVE

    def _render(self) -> None:
        for g in self._groups:
            self._prefs.remove(g)
        self._groups.clear()
        self._prefs.remove(self._help_group)
        titles = dict(doctor.GROUPS)
        order = [g for g, _ in doctor.GROUPS] + sorted({c.group for c in self._checks} - set(titles))
        for gid in order:
            rows = [c for c in self._checks if c.group == gid]
            if not rows:
                continue
            group = Adw.PreferencesGroup(title=titles.get(gid, gid.title()))
            for c in rows:
                group.add(self._row(c))
            self._prefs.add(group)
            self._groups.append(group)
        self._prefs.add(self._help_group)

    def _row(self, c: doctor.Check) -> Adw.ActionRow:
        row = Adw.ActionRow(title=c.title, subtitle=c.detail, use_markup=False)
        status = c.status if c.status in ICONS else "warn"
        badge = Gtk.Image(icon_name=ICONS[status], valign=Gtk.Align.CENTER,
                          css_classes=["ail-health-badge", status])
        badge.update_property([Gtk.AccessibleProperty.LABEL],
                              [{"ok": "OK", "warn": "Warning", "fail": "Problem", "info": "Information"}[status]])
        row.add_prefix(badge)
        if c.fix_id and self._can_fix(c.fix_id):
            btn = Gtk.Button(label=FIX_LABELS.get(c.fix_id, "Fix"), valign=Gtk.Align.CENTER)
            if c.status in ("warn", "fail"):
                btn.add_css_class("suggested-action")
            btn.connect("clicked", self._on_fix, c.fix_id)
            row.add_suffix(btn)
        return row

    def _can_fix(self, fix_id: str) -> bool:
        if fix_id == "start_ollama":
            return callable(getattr(getattr(self.ctx, "ollama", None), "start", None))
        return fix_id in FIX_LABELS

    # fixes

    def _on_fix(self, button: Gtk.Button, fix_id: str) -> None:
        try:
            if fix_id == "enable_autostart":
                self._fix_autostart()
            elif fix_id == "start_ollama":
                button.set_sensitive(False)
                self.ctx.ollama.start()
                self._toast("Starting Ollama…")
                self._schedule_recheck(4000)
            elif fix_id == "open_models":
                root = self.get_root()
                if hasattr(root, "show_page"):
                    root.show_page("models")
                else:
                    self._toast("Open the Models page to download them")
        except Exception as exc:
            log.exception("fix %s failed", fix_id)
            self._toast(f"Couldn't fix it: {exc}")

    def _fix_autostart(self) -> None:
        if not self.ctx.screenshot_mode:
            autostart.set_enabled(True)
        if not self.ctx.settings.get("autostart"):
            self.ctx.settings.set("autostart", True)
        self._toast("AI Linux will start when you sign in")
        self.refresh()

    def _schedule_recheck(self, ms: int) -> None:
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)

        def fire():
            self._recheck_id = 0
            self.refresh()
            return GLib.SOURCE_REMOVE

        self._recheck_id = GLib.timeout_add(ms, fire)

    def _watch_ollama(self) -> None:
        mgr = getattr(self.ctx, "ollama", None)
        if mgr is None:
            return
        try:
            self._ollama_handler = mgr.connect("status-changed", lambda *_: self._schedule_recheck(300))
        except Exception:
            log.debug("ollama manager has no status-changed signal", exc_info=True)

    def _on_destroy(self, *_):
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)
            self._recheck_id = 0
        if self._ollama_handler:
            try:
                self.ctx.ollama.disconnect(self._ollama_handler)
            except Exception:
                pass
            self._ollama_handler = 0

    # help actions

    def _on_copy(self, *_):
        try:
            text = doctor.report_text(self._checks)
            self.get_clipboard().set(text)
            self._toast("Diagnostic report copied")
        except Exception:
            log.exception("copying the report")
            self._toast("Couldn't copy the report")

    def _on_open_logs(self, *_):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._toast(f"Couldn't create the logs folder: {exc.strerror}")
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(LOG_DIR)))

        def done(src, result):
            try:
                src.launch_finish(result)
            except GLib.Error as exc:
                if not exc.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    log.warning("opening logs folder: %s", exc.message)
                    self._toast("Couldn't open the logs folder")

        launcher.launch(self.get_root(), None, done)

    def _toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3))
