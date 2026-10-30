"""Application entry: single instance, tray-resident, window on demand."""

from __future__ import annotations

import importlib
import logging
import logging.handlers
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ai_linux import __version__  # noqa: E402
from ai_linux.config import APP_ID, APP_NAME, HISTORY_DB, ICON_NAME, LOG_DIR, get_settings  # noqa: E402
from ai_linux.context import AppContext  # noqa: E402
from ai_linux.schema import on_ac_power  # noqa: E402
from ai_linux.controller import Controller  # noqa: E402
from ai_linux.state import AssistantState, Bus  # noqa: E402
from ai_linux.ui import theme  # noqa: E402

log = logging.getLogger("ai_linux")


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "app.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("ai_linux")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stderr))


def _optional(module: str, attr: str, *args):
    """Build an optional component; a missing or failing part is logged, never fatal."""
    try:
        return getattr(importlib.import_module(module), attr)(*args)
    except Exception:
        log.exception("component %s.%s unavailable", module, attr)
        return None


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.ctx: AppContext | None = None
        self.window = None
        self._held = False
        opts = [
            ("background", "Start hidden in the tray"),
            ("show", "Show the window"),
            ("toggle", "Start listening, or cancel the current turn"),
            ("mute", "Stop listening"),
            ("unmute", "Resume listening"),
            ("quit", "Quit the running instance"),
            ("demo", "Drive the UI with simulated turns (development)"),
            ("version", "Print the version"),
        ]
        for name, desc in opts:
            self.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, desc, None)
        for name, desc in (
            ("screenshot", "Render the window to this PNG and quit (development)"),
            ("page", "Page to show with --screenshot"),
            ("state", "Assistant state to show with --screenshot"),
        ):
            self.add_main_option(name, 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, desc, None)

    def do_startup(self):
        Adw.Application.do_startup(self)
        settings = get_settings()
        bus = Bus()
        self.ctx = AppContext(app=self, settings=settings, bus=bus, controller=Controller(settings, bus))
        theme.install()
        theme.apply_color_scheme(settings)
        try:
            from ai_linux.knowledge import Knowledge, migrate_about_me
            migrate_about_me(settings, Knowledge())  # the old About you text becomes facts you keep
        except Exception:
            log.exception("moving About you into Knowledge")
        self.ctx.history = _optional("ai_linux.history_store", "HistoryStore", HISTORY_DB)
        if self.ctx.history is not None:
            bus.connect("turn-added", lambda _b, t: self.ctx.history.add(t))
            bus.connect("turn-updated", lambda _b, t: self.ctx.history.update(t))
        self.ctx.ollama = _optional("ai_linux.ollama", "OllamaManager", self.ctx)
        self.ctx.controller.on_confirm = self._confirm_dialog
        self.ctx.todoist = _optional("ai_linux.todoist_link", "TodoistLink", settings,
                                     self.ctx.controller.set_secret)
        GLib.idle_add(self._load_tokens)  # after the command line, so screenshot mode never opens the keyring
        self._add_actions()
        GLib.timeout_add_seconds(5, self._poll_power)

    def _load_tokens(self) -> bool:
        if self.ctx.todoist is not None and not self.ctx.screenshot_mode:
            self.ctx.todoist.load()
        return False

    def _poll_power(self) -> bool:
        """Auto profile: battery or charger settings follow the plug (a few sysfs reads)."""
        try:
            self.ctx.settings.set_on_ac(on_ac_power())
        except Exception:
            log.exception("power check")
        return True

    def _desktop_integration(self) -> None:
        """Tray, pill and hotkey register with the real session, so never in screenshot mode."""
        if self.ctx.screenshot_mode or self.ctx.tray is not None:
            return
        self.ctx.tray = _optional("ai_linux.tray", "Tray", self.ctx)
        self.ctx.pill = _optional("ai_linux.ui.pill", "Pill", self.ctx)
        self.ctx.hotkey = _optional("ai_linux.hotkey", "Hotkey", self.ctx)

    def _add_actions(self) -> None:
        c = self.ctx.controller
        for name, cb in (
            ("show", lambda *_: self.activate()),
            ("toggle-listen", lambda *_: c.toggle_listening()),
            ("mute", lambda *_: c.mute(True)),
            ("unmute", lambda *_: c.mute(False)),
            ("preferences", lambda *_: self._preferences()),
            ("knowledge", lambda *_: self._knowledge()),
            ("about", lambda *_: self._about()),
            ("quit", lambda *_: self._quit()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.preferences", ["<Control>comma"])

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        c = self.ctx.controller
        if opts.get("version"):
            cmdline.print_literal(f"{APP_NAME} {__version__}\n")
            return 0
        if opts.get("quit"):
            self._quit()
            return 0
        if opts.get("screenshot"):
            self.ctx.screenshot_mode = True
            from ai_linux import devtools

            devtools.screenshot(self, opts["screenshot"], opts.get("page"), opts.get("state"))
            return 0
        self._desktop_integration()
        if opts.get("demo"):
            c.enable_demo()
        elif not cmdline.get_is_remote():
            c.start()
        if opts.get("toggle"):
            c.toggle_listening()
        if opts.get("mute"):
            c.mute(True)
        if opts.get("unmute"):
            c.mute(False)
        if self.ctx.settings.get("keep_running") and not self._held:
            self.hold()  # stay alive in the tray with no window
            self._held = True
        remote_cmd = any(opts.get(k) for k in ("toggle", "mute", "unmute"))
        if opts.get("show") or not (opts.get("background") or remote_cmd):
            self.activate()
        return 0

    def do_activate(self):
        win = self.get_window()
        win.present()
        onboarding = _optional_module("ai_linux.ui.onboarding")
        if onboarding and not self.ctx.settings.get("onboarding_done") and not self.ctx.screenshot_mode:
            GLib.idle_add(lambda: onboarding.maybe_show(win, self.ctx) and False)

    def get_window(self):
        if self.window is None:
            self.window = _optional("ai_linux.ui.window", "MainWindow", self.ctx)
            if self.window is None:
                self.window = _fallback_window(self)
        return self.window

    def _preferences(self) -> None:
        prefs = _optional_module("ai_linux.ui.preferences")
        if prefs:
            prefs.show_preferences(self.get_window(), self.ctx)

    def _knowledge(self) -> None:
        page = _optional_module("ai_linux.ui.knowledge")
        if page:
            page.show_knowledge(self.get_window(), self.ctx)

    def _about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME, application_icon=ICON_NAME, version=__version__,
            developer_name="Digvijaysing", license_type=Gtk.License.MIT_X11,
            comments="Local-first voice assistant for the Linux desktop.",
        )
        from ai_linux import credits
        for c in credits.by_kind("code"):  # their code ships with ours, so their licences are listed in full
            about.add_legal_section(c.name, GLib.markup_escape_text(f"{c.role}. {c.owner}"),
                                    getattr(Gtk.License, credits.gtk_licence(c) or "CUSTOM"), None)
        for kind, title, _description in credits.KINDS:  # every credit, the best-known first
            entries = sorted(credits.by_kind(kind), key=lambda c: not c.featured)
            about.add_acknowledgement_section(title, [f"{c.name} {c.url}" for c in entries])
        about.add_acknowledgement_section("Thank you", ["The open-source community"])
        about.present(self.get_window())

    def _confirm_dialog(self, event: dict, respond):
        """A risky action waits for an on-screen yes; no answer within 55 s means no."""
        from ai_linux.tools_catalog import title_for
        win = self._dialog_parent(self.window)
        if win is not None:
            win.present()
        command = event.get("command") or ""
        if event.get("detail"):  # not a risky action: the router wasn't sure what was meant
            dialog = Adw.AlertDialog(heading=event["detail"], body="Phoenix wasn't sure what you meant.")
            dialog.add_response("deny", "No")
            dialog.add_response("allow", "Yes")
            dialog.set_default_response("allow")
            dialog.set_close_response("deny")
            return self._confirm_finish(dialog, win, respond)
        dialog = Adw.AlertDialog(heading=f"Allow {title_for(event.get('tool', ''))}?",
                                 body="The assistant wants to run this on your computer:" if command else
                                 "The assistant wants to take this action.")
        if command:
            label = Gtk.Label(label=command, wrap=True, selectable=True, xalign=0)
            label.add_css_class("monospace")
            dialog.set_extra_child(label)
        dialog.add_response("deny", "Don't Allow")
        dialog.add_response("allow", "Allow")
        dialog.set_response_appearance("allow", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("deny")
        dialog.set_close_response("deny")
        return self._confirm_finish(dialog, win, respond)

    @staticmethod
    def _dialog_parent(win):
        """The main window if it is on screen, else None: Adw.Dialog.present(None) opens just the question."""
        if win is None or not win.get_visible():
            return None
        suspended = getattr(win, "is_suspended", None)  # GTK 4.12+: minimised or on another workspace
        return None if suspended is not None and suspended() else win

    @staticmethod
    def _confirm_finish(dialog, win, respond):
        answered = {"done": False}

        def finish(allowed: bool) -> None:
            if not answered["done"]:
                answered["done"] = True
                respond(allowed)

        dialog.connect("response", lambda _d, r: finish(r == "allow"))

        def expire():
            if not answered["done"]:
                finish(False)
                dialog.force_close()
            return False

        def settled():  # answered out loud: the engine already has it
            answered["done"] = True
            dialog.force_close()

        GLib.timeout_add_seconds(55, expire)
        dialog.present(win)
        return settled

    def _quit(self) -> None:
        for part in (self.ctx.tray, self.ctx.pill, self.ctx.hotkey):
            try:
                if part is not None:
                    part.close()
            except Exception:
                log.exception("closing %s", part)
        self.ctx.controller.stop()
        self.quit()


def _optional_module(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        log.exception("module %s unavailable", name)
        return None


def _fallback_window(app: App) -> Adw.ApplicationWindow:
    win = Adw.ApplicationWindow(application=app, title=APP_NAME, default_width=900, default_height=640)
    win.set_content(Adw.StatusPage(
        title=APP_NAME, icon_name="dialog-warning-symbolic",
        description="The main window failed to load. See ~/.local/state/ai-linux/logs/app.log",
    ))
    return win


def main(argv: list[str]) -> int:
    _setup_logging()
    app = App()
    return app.run(argv)


__all__ = ["App", "AssistantState", "main"]
