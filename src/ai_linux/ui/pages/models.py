"""Models page: the Ollama runtime, the small (CPU) and big (GPU) model slots, and how they behave."""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import threading
from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux import downloads, models_catalog  # noqa: E402
from ai_linux.config import DATA_DIR, ProfileView  # noqa: E402
from ai_linux.ui.profile_group import ProfileGroup  # noqa: E402

log = logging.getLogger(__name__)

POLICIES = (("on_demand", "On demand"), ("instant", "Always loaded"))
KEEP_ALIVE = (("2m", "2 minutes"), ("5m", "5 minutes"), ("10m", "10 minutes"), ("30m", "30 minutes"),
              ("0", "Unload right away"))
SMALL_DEVICES = (("GPU", "Intel GPU"), ("CPU", "Processor (CPU)"))
BIG_DEVICES = (("gpu", "NVIDIA GPU"), ("cpu", "Processor (CPU), slow"))
CONTEXT = ((2048, "2K tokens"), (4096, "4K tokens"), (8192, "8K tokens"), (16384, "16K tokens"))
BUSY_TITLES = {"starting": "Starting", "stopping": "Stopping", "restarting": "Restarting",
               "installing": "Installing"}
SLOT_TEXT = {
    "small": ("Small Model", "Picks the command for each request without waking the NVIDIA GPU. With None, "
                             "the big model picks it too."),
    "speech": ("Speech Recognition", "Turns what you say into text, on the CPU."),
    "big": ("Big Model", "Answers questions and chat, reads the screen, writes replies, and takes over when "
                         "the small model is unsure."),
}
_FRACTION = re.compile(r"(\.\d{6})\d+")  # Ollama sends nanoseconds, Python parses microseconds


def _tag(text: str, *classes: str) -> Gtk.Label:
    return Gtk.Label(label=text, valign=Gtk.Align.CENTER, css_classes=["ail-models-tag", *classes])


def _size(n: int) -> str:
    return GLib.format_size(max(0, int(n or 0)))


def _gb(value: float) -> str:
    return f"{value:g} GB"


def _norm(tag: str) -> str:
    tag = (tag or "").strip()
    return tag if ":" in tag.rsplit("/", 1)[-1] else f"{tag}:latest"


def _unloads_in(expires_at: str) -> str:
    """'2026-10-27T13:05:00.1234+05:30' -> 'unloads in 9 min'; far future -> 'stays loaded'."""
    try:
        when = dt.datetime.fromisoformat(_FRACTION.sub(r"\1", expires_at.replace("Z", "+00:00")))
        left = (when - dt.datetime.now(when.tzinfo)).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return ""
    if left > 86400 * 365:
        return "stays loaded"
    if left < 60:
        return "unloads in under a minute"
    return f"unloads in {int(left // 60)} min"


def _loaded_text(m: dict) -> str:
    size, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
    if vram <= 0:
        where = f"{_size(size)} on the CPU"
    elif vram >= size:
        where = f"{_size(vram)} on the GPU"
    else:
        where = f"{_size(vram)} of {_size(size)} on the GPU"
    parts = [m.get("name", "?"), where, _unloads_in(m.get("expires_at", ""))]
    return " · ".join(p for p in parts if p)


@dataclass
class _Job:
    cancel: Callable[[], None]
    done: int = 0
    total: int = 0
    status: str = "Starting"


class _ModelRow(Adw.PreferencesRow):
    """One catalog entry: radio, facts with a Recommended tag, and its download state.

    A custom row so the tag sits beside the name and the progress bar spans the text column.
    """

    def __init__(self, spec: models_catalog.ModelSpec, group: Gtk.CheckButton | None):
        super().__init__(title=spec.name, use_markup=False, activatable=True)
        self.add_css_class("ail-models-row")
        self.spec = spec
        self.update_property([Gtk.AccessibleProperty.LABEL], [spec.name])
        need = ("Runs on the CPU" if spec.runtime == "onnx" else "Intel GPU or CPU" if spec.vram_gb <= 0
                else f"Needs about {_gb(spec.vram_gb)} of GPU memory")

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=group)
        self.check.update_property([Gtk.AccessibleProperty.LABEL], [f"Use {spec.name}"])

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label=spec.name, xalign=0, wrap=True, css_classes=["ail-models-name"]))
        if spec.recommended:
            head.append(_tag("Recommended", "accent"))
        text.append(head)
        text.append(Gtk.Label(label=spec.summary, xalign=0, wrap=True, css_classes=["ail-models-summary"]))
        text.append(Gtk.Label(label=f"{spec.licence} · {_gb(spec.download_gb)} download · {need}", xalign=0,
                              wrap=True, css_classes=["caption", "dim-label"]))
        if spec.note:
            text.append(Gtk.Label(label=spec.note, xalign=0, wrap=True,
                                  css_classes=["caption", "dim-label", "ail-models-note"]))

        bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, css_classes=["ail-models-progress-box"])
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.add_css_class("ail-models-progress")  # css_classes= would drop "horizontal"
        self.progress_label = Gtk.Label(xalign=0, css_classes=["caption", "dim-label", "ail-models-progress-label"])
        bars.append(self.progress_bar)
        bars.append(self.progress_label)
        self.progress = Gtk.Revealer(child=bars, reveal_child=False,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        text.append(self.progress)

        self.stack = Gtk.Stack(valign=Gtk.Align.CENTER, hhomogeneous=False, vhomogeneous=False,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        installed = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, css_classes=["ail-models-installed"])
        installed.append(Gtk.Image(icon_name="object-select-symbolic"))
        installed.append(Gtk.Label(label="Installed"))
        self.stack.add_named(installed, "installed")

        self.download_btn = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        self.stack.add_named(self.download_btn, "download")

        self.cancel_btn = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.cancel_btn.update_property([Gtk.AccessibleProperty.LABEL], [f"Cancel the {spec.name} download"])
        self.stack.add_named(self.cancel_btn, "progress")

        failed = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.error_icon = Gtk.Image(icon_name="dialog-error-symbolic", css_classes=["ail-models-error-icon"])
        self.retry_btn = Gtk.Button(label="Retry", valign=Gtk.Align.CENTER)
        failed.append(self.error_icon)
        failed.append(self.retry_btn)
        self.stack.add_named(failed, "failed")

        box = Gtk.Box(spacing=12, css_classes=["ail-models-row-box"])
        box.append(self.check)
        box.append(text)
        box.append(self.stack)
        self.set_child(box)
        self.connect("notify::parent", self._on_parent)

    def _on_parent(self, *_):
        # the group's list box reports clicks; mirror what Adw.ActionRow does with its activatable widget
        parent = self.get_parent()
        if isinstance(parent, Gtk.ListBox) and not getattr(parent, "_ail_models_hooked", False):
            parent._ail_models_hooked = True
            parent.connect("row-activated", lambda _l, row: isinstance(row, _ModelRow) and row.check.activate())

    def show_installed(self) -> None:
        self.progress.set_reveal_child(False)
        self.stack.set_visible_child_name("installed")

    def show_download(self, sensitive: bool, why: str | None) -> None:
        self.progress.set_reveal_child(False)
        self.download_btn.set_sensitive(sensitive)
        self.download_btn.set_tooltip_text(why or f"Download {_gb(self.spec.download_gb)}")
        self.stack.set_visible_child_name("download")

    def show_progress(self, job: _Job) -> None:
        if job.total > 0:
            frac = min(1.0, job.done / job.total)
            self.progress_bar.set_fraction(frac)
            self.progress_label.set_label(f"{job.status} · {frac:.0%} · {_size(job.done)} of {_size(job.total)}")
        else:
            self.progress_bar.pulse()
            self.progress_label.set_label(job.status)
        self.cancel_btn.set_sensitive(job.status != "Cancelling")
        self.progress.set_reveal_child(True)
        self.stack.set_visible_child_name("progress")

    def show_failed(self, error: str) -> None:
        self.progress.set_reveal_child(False)
        self.error_icon.set_tooltip_text(error)
        self.retry_btn.set_tooltip_text(error)
        self.stack.set_visible_child_name("failed")


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Models", tag="models")
        self.ctx = ctx
        self.settings = ProfileView(ctx.settings)  # per-profile rows edit the profile picked at the top
        self._handlers: list[tuple[object, int]] = []
        self._syncing = False  # true while rows follow settings, so they do not write back
        self._none_check: Gtk.CheckButton | None = None
        self._polling = False
        self._rows: dict[str, _ModelRow] = {}
        self._jobs: dict[str, _Job] = {}
        self._errors: dict[str, str] = {}
        self._jiwo_installed = False
        self._speech_installed: dict[str, bool] = {}
        self._install_handle = None
        self._install_progress: tuple[int, int, str] = (0, 0, "Starting")
        self._installed_key: tuple | None = None
        self._installed_rows: list[Gtk.Widget] = []

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check Again")
        self._refresh_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Check Again"])
        self._refresh_btn.connect("clicked", lambda *_: self._refresh())
        header.pack_end(self._refresh_btn)
        view.add_top_bar(header)

        prefs = Adw.PreferencesPage()
        prefs.add_css_class("ail-models")
        prefs.set_description("By default, on battery a small model picks the command and the big one wakes "
                              "only when needed; on the charger the big model does everything. Change either "
                              "below.")
        prefs.add(self._build_ollama())
        self._profiles = ProfileGroup(self.settings)
        prefs.add(self._profiles)
        for slot in ("speech", "small", "big"):
            prefs.add(self._profiles.titled(self._build_slot(slot), SLOT_TEXT[slot][0]))
        prefs.add(self._profiles.titled(self._build_behaviour(), "Behaviour"))
        prefs.add(self._build_storage())
        self._toasts = Adw.ToastOverlay(child=prefs)
        view.set_content(self._toasts)
        self.set_child(view)

        if self.ctx.ollama is not None:
            self._connect(self.ctx.ollama, "status-changed", lambda *_: self._sync())
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", self._on_destroy)
        self._check_jiwo()
        self._sync()

    # helpers

    @property
    def _status(self) -> dict:
        mgr = self.ctx.ollama
        return dict(getattr(mgr, "status", None) or {}) if mgr is not None else {}

    def _connect(self, obj, signal: str, cb) -> None:
        self._handlers.append((obj, obj.connect(signal, cb)))

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

        self._connect(self.settings, f"changed::{key}", on_changed)

    def _toast(self, text: str) -> None:
        toast = Adw.Toast(title=text, timeout=4)
        toast.set_use_markup(False)
        root = self.get_root()
        if hasattr(root, "add_toast"):
            root.add_toast(toast)  # the window's overlay survives a page switch
        else:
            self._toasts.add_toast(toast)

    def _alert(self, heading: str, body: str, action: str, label: str, destructive: bool,
               on_yes: Callable[[], None]) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response(action, label)
        dialog.set_response_appearance(
            action, Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel" if destructive else action)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == action and on_yes())
        dialog.present(self.get_root() or self)

    @staticmethod
    def _free_space_text() -> str:
        probe = DATA_DIR
        while not probe.exists() and probe != probe.parent:  # first run: the data folder is not made yet
            probe = probe.parent
        try:
            return f"{_size(shutil.disk_usage(probe).free)} free on this disk"
        except OSError:
            return ""

    # ollama card

    def _build_ollama(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Ollama", description="Runs the language models on this computer.")
        self._ollama_group = group

        self._status_row = Adw.ActionRow(use_markup=False)
        self._status_row.add_css_class("ail-models-status")
        self._status_icon = Gtk.Image(icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER,
                                      css_classes=["ail-models-status-icon"])
        spinner_tile = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["ail-models-status-icon", "busy"])
        spinner_tile.append(Adw.Spinner(width_request=16, height_request=16, margin_start=8, margin_end=8,
                                        margin_top=8, margin_bottom=8))
        self._status_tile = Gtk.Stack(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)  # the spinner replaces the icon while busy
        self._status_tile.add_named(self._status_icon, "icon")
        self._status_tile.add_named(spinner_tile, "busy")
        self._status_row.add_prefix(self._status_tile)
        buttons = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self._install_btn = Gtk.Button(label="Install…")
        self._install_btn.add_css_class("suggested-action")
        self._install_btn.connect("clicked", lambda *_: self._confirm_install())
        self._start_btn = Gtk.Button(label="Start")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.connect("clicked", lambda *_: self._action("start"))
        self._restart_btn = Gtk.Button(label="Restart")
        self._restart_btn.connect("clicked", lambda *_: self._action("restart"))
        self._stop_btn = Gtk.Button(label="Stop")
        self._stop_btn.connect("clicked", lambda *_: self._action("stop"))
        for w in (self._install_btn, self._start_btn, self._restart_btn, self._stop_btn):
            buttons.append(w)
        self._status_row.add_suffix(buttons)
        group.add(self._status_row)

        self._install_row = Adw.ActionRow(title="Installing Ollama", use_markup=False, visible=False)
        self._install_bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self._install_bar.add_css_class("ail-models-progress")
        cancel = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Cancel Install",
                            valign=Gtk.Align.CENTER)
        cancel.add_css_class("flat")
        cancel.add_css_class("circular")
        cancel.update_property([Gtk.AccessibleProperty.LABEL], ["Cancel Install"])
        cancel.connect("clicked", lambda *_: self._install_handle and self._install_handle.cancel())
        self._install_row.add_suffix(self._install_bar)
        self._install_row.add_suffix(cancel)
        group.add(self._install_row)

        group.add(self._build_context_row())

        self._version_row = self._property_row("Version")
        self._address_row = self._property_row("Address")
        self._loaded_row = self._property_row("Loaded Now")
        for row in (self._version_row, self._address_row, self._loaded_row):
            group.add(row)

        self._error_row = Adw.ActionRow(title="Something went wrong", use_markup=False, subtitle_lines=0,
                                        visible=False)
        self._error_row.add_css_class("ail-models-error")
        self._error_row.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic", valign=Gtk.Align.CENTER,
                                             css_classes=["ail-models-error-icon"]))
        dismiss = Gtk.Button(label="Dismiss", valign=Gtk.Align.CENTER)
        dismiss.add_css_class("flat")
        dismiss.connect("clicked", lambda *_: self._dismiss_error())
        self._error_row.add_suffix(dismiss)
        group.add(self._error_row)
        return group

    @staticmethod
    def _property_row(title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, use_markup=False, subtitle_lines=0, subtitle_selectable=True,
                            visible=False)
        row.add_css_class("property")
        return row

    def _sync_ollama(self) -> None:
        mgr, st = self.ctx.ollama, self._status
        if mgr is None:
            self._status_row.set_title("Ollama controls are unavailable")
            self._status_row.set_subtitle("The Ollama manager failed to start. Details are in the app log.")
            self._set_status_icon("missing")
            for w in (self._install_btn, self._start_btn, self._restart_btn, self._stop_btn):
                w.set_visible(False)
            for row in (self._version_row, self._address_row, self._loaded_row, self._install_row,
                        self._error_row):
                row.set_visible(False)
            self._refresh_btn.set_sensitive(False)
            return

        busy, running, installed = st.get("busy"), bool(st.get("running")), bool(st.get("installed"))
        external = running and st.get("owner") != "app"
        local = st.get("local", True)
        if busy:
            title, sub, icon = f"{BUSY_TITLES.get(busy, busy.capitalize())}…", "", "busy"
            if busy == "starting":
                sub = "The first start can take half a minute while the GPU is found."
        elif not st.get("checked"):
            title, sub, icon = "Checking…", "", "busy"
        elif running:
            title, icon = "Running", "running"
            sub = ("Started outside the app, so the app leaves it running" if external
                   else "Started by the app")
        elif installed:
            title, sub, icon = "Stopped", "Start it so the assistant can think and download models.", "stopped"
        elif local:
            title, icon = "Not installed", "missing"
            sub = "The app can install its own copy. No administrator password is needed."
        else:
            title, sub, icon = "Not reachable", "Nothing answers at this address.", "missing"
        self._status_row.set_title(title)
        self._status_row.set_subtitle(sub)
        self._set_status_icon(icon)

        show_install = local and not installed and st.get("checked") and busy in (None, "installing")
        self._install_btn.set_visible(bool(show_install) and busy != "installing")
        self._start_btn.set_visible(installed and not running and local and busy not in ("stopping",))
        self._restart_btn.set_visible(running and local)
        self._stop_btn.set_visible(running and local)
        for btn in (self._start_btn, self._restart_btn, self._stop_btn, self._install_btn):
            btn.set_sensitive(not busy)
        for btn in (self._restart_btn, self._stop_btn):
            btn.set_tooltip_text("Started outside the app, so the app leaves it alone" if external else None)
            if external:
                btn.set_sensitive(False)

        installing = busy == "installing" or self._install_handle is not None
        self._install_row.set_visible(installing)
        if installing:
            self._render_install_progress()

        version = st.get("version")
        self._version_row.set_visible(bool(version))
        if version:
            self._version_row.set_subtitle(version + (" (the app's own copy)" if st.get("managed") else ""))
        self._address_row.set_visible(st.get("checked", False))
        self._address_row.set_subtitle(st.get("url") or "")
        self._loaded_row.set_visible(running)
        loaded = st.get("loaded") or []
        self._loaded_row.set_subtitle("\n".join(_loaded_text(m) for m in loaded) if loaded else "None")

        error = st.get("error")
        self._error_row.set_visible(bool(error))
        if error:
            self._error_row.set_subtitle(error)
        self._refresh_btn.set_sensitive(True)

    def _set_status_icon(self, kind: str) -> None:
        icons = {"running": "media-playback-start-symbolic", "stopped": "media-playback-stop-symbolic",
                 "missing": "dialog-warning-symbolic", "busy": "content-loading-symbolic"}
        self._status_tile.set_visible_child_name("busy" if kind == "busy" else "icon")
        self._status_icon.set_from_icon_name(icons[kind])
        for k in icons:
            self._status_icon.remove_css_class(k)
        self._status_icon.add_css_class(kind)

    def _action(self, name: str) -> None:
        mgr = self.ctx.ollama
        if mgr is None:
            return

        def done(ok, err):
            if ok:
                self._toast({"start": "Ollama is running", "stop": "Ollama stopped",
                             "restart": "Ollama restarted"}[name])

        getattr(mgr, name)(done)
        self._sync()

    def _dismiss_error(self) -> None:
        mgr = self.ctx.ollama
        if mgr is not None and hasattr(mgr, "clear_error"):
            mgr.clear_error()

    def _confirm_install(self) -> None:
        try:
            from ai_linux import ollama as ollama_mod

            asset, version = ollama_mod.install_asset(), ollama_mod.OLLAMA_PIN
        except Exception:
            log.exception("reading the Ollama release pin")
            asset, version = None, "the pinned"
        if asset is None:
            self._toast("There is no official Ollama build for this processor")
            return
        size = asset[1]
        body = (f"This downloads the official Ollama {version} release ({_size(size)}) and needs about "
                f"{_size(int(size * 3.5))} of free space while it unpacks. It is stored in your home folder "
                f"and runs as your user, so no administrator password is needed.")
        free = self._free_space_text()
        if free:
            body += f"\n\n{free[0].upper()}{free[1:]}."
        self._alert("Install Ollama?", body, "install", "Install", False, self._start_install)

    def _start_install(self) -> None:
        mgr = self.ctx.ollama
        if mgr is None or self._install_handle is not None:
            return
        self._install_progress = (0, 0, "Starting")

        def progress(done, total, status):
            self._install_progress = (done, total, status)
            self._render_install_progress()

        def finished(ok, err):
            self._install_handle = None
            if ok:
                self._toast("Ollama is installed")
            elif err == downloads.CANCELLED:
                self._toast("Install cancelled. It resumes where it stopped next time.")
            self._sync()

        self._install_handle = mgr.install(progress, finished)
        self._sync()

    def _render_install_progress(self) -> None:
        done, total, status = self._install_progress
        if total > 0:
            self._install_bar.set_fraction(min(1.0, done / total))
            self._install_row.set_subtitle(f"{status} · {_size(done)} of {_size(total)}")
        else:
            self._install_bar.pulse()
            self._install_row.set_subtitle(status)

    # model slots

    def _build_slot(self, slot: str) -> Adw.PreferencesGroup:
        title, desc = SLOT_TEXT[slot]
        group = Adw.PreferencesGroup(title=title, description=desc)
        key = models_catalog.SLOT_KEYS[slot]
        first: Gtk.CheckButton | None = None
        for spec in models_catalog.for_slot(slot):
            row = _ModelRow(spec, first)
            first = first or row.check
            row.check.set_active(self.settings.get(key) == spec.id)
            row.check.connect("toggled", self._on_pick, key, spec)
            row.download_btn.connect("clicked", lambda *_, s=spec: self._confirm_download(s))
            row.retry_btn.connect("clicked", lambda *_, s=spec: self._confirm_download(s))
            row.cancel_btn.connect("clicked", lambda *_, s=spec: self._cancel_download(s.id))
            self._rows[spec.id] = row
            group.add(row)
        if slot == "small":
            self._none_check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=first,
                                               active=self.settings.get(key) in ("none", ""))
            self._none_check.update_property([Gtk.AccessibleProperty.LABEL], ["Use no small model"])
            self._none_check.connect("toggled", lambda c: c.get_active() and self._store(key, "none"))
            none = Adw.ActionRow(title="None", subtitle="The big model picks every command: more accurate, but "
                                                        "it keeps the NVIDIA GPU awake", activatable_widget=self._none_check)
            none.add_prefix(self._none_check)
            group.add(none)
        if slot in ("small", "big"):
            dev_key, devices = ("small_device", SMALL_DEVICES) if slot == "small" else ("big_device", BIG_DEVICES)
            device = self._combo(dev_key, "Runs On", devices,
                                 "The Intel GPU leaves the NVIDIA GPU asleep; the processor is the fallback."
                                 if slot == "small" else
                                 "The processor keeps the graphics card free, but answers take many seconds.")
            device.set_use_subtitle(False)
            device.add_prefix(Gtk.Image(icon_name="computer-symbolic" if slot == "small" else "video-display-symbolic"))
            group.add(device)
        self._watch(key, lambda k=key, s=slot: self._follow_pick(k, s))
        return group

    def _on_pick(self, check: Gtk.CheckButton, key: str, spec: models_catalog.ModelSpec) -> None:
        if not check.get_active() or self._syncing:
            return
        self._store(key, spec.id)
        if not self._is_installed(spec) and spec.id not in self._jobs:
            self._toast(f"{spec.name} is selected. Download it before the assistant can use it.")

    def _follow_pick(self, key: str, slot: str) -> None:
        current = self.settings.get(key)
        for spec in models_catalog.for_slot(slot):
            self._rows[spec.id].check.set_active(spec.id == current)
        if slot == "small" and self._none_check is not None:
            self._none_check.set_active(current in ("none", ""))

    def _is_installed(self, spec: models_catalog.ModelSpec) -> bool:
        if spec.runtime == "ollama":
            mgr = self.ctx.ollama
            try:
                return bool(mgr is not None and mgr.has_model(spec.source))
            except Exception:
                log.exception("has_model %s", spec.source)
                return False
        if spec.runtime == "openvino":
            return self._jiwo_installed
        if spec.runtime == "onnx":
            return self._speech_installed.get(spec.id, False)
        return False

    def _check_jiwo(self) -> None:
        try:
            self._jiwo_installed = downloads.jiwo_installed()  # a few stat calls, cheap enough here
            self._speech_installed = {e: downloads.speech_installed(e) for e in downloads.SPEECH_FILES}
        except Exception:
            log.exception("checking the downloaded model files")
            self._jiwo_installed = False

    def _sync_models(self) -> None:
        st = self._status
        for model_id, row in self._rows.items():
            spec = row.spec
            job = self._jobs.get(model_id)
            if job is not None:
                row.show_progress(job)
            elif self._is_installed(spec):
                self._errors.pop(model_id, None)
                row.show_installed()
            elif model_id in self._errors:
                row.show_failed(self._errors[model_id])
            elif spec.runtime == "ollama":
                if self.ctx.ollama is None:
                    row.show_download(False, "Ollama controls are unavailable")
                elif not st.get("running"):
                    why = "Start Ollama to download" if st.get("installed") else "Install Ollama to download"
                    row.show_download(False, why)
                else:
                    row.show_download(True, None)
            elif spec.runtime == "openvino" and not downloads.JIWO_PUBLISHED:
                row.show_download(False, "Not published for download yet")
            else:
                row.show_download(True, None)

    def _confirm_download(self, spec: models_catalog.ModelSpec) -> None:
        if spec.id in self._jobs:
            return
        if spec.runtime == "ollama":
            origin = f"the Ollama library ({spec.source})"
        elif spec.runtime == "onnx":
            origin = "the GLaDOS project's release on GitHub"
        else:
            origin = f"Hugging Face ({spec.source})"
        body = f"{_gb(spec.download_gb)} comes from {origin} and is stored on this computer."

        free = self._free_space_text()
        if free:
            body += f"\n\n{free[0].upper()}{free[1:]}."
        self._alert(f"Download {spec.name}?", body, "download", f"Download {_gb(spec.download_gb)}", False,
                    lambda: self._start_download(spec.id))

    def _start_download(self, model_id: str) -> None:
        spec = models_catalog.get(model_id)
        if spec is None or model_id in self._jobs:
            return
        self._errors.pop(model_id, None)

        def progress(done, total, status="Downloading"):
            job = self._jobs.get(model_id)
            if job is None:
                return
            job.done, job.total, job.status = int(done or 0), int(total or 0), status or "Downloading"
            self._rows[model_id].show_progress(job)

        def finished(ok, err):
            self._jobs.pop(model_id, None)
            if spec.runtime in ("openvino", "onnx"):
                self._check_jiwo()
            if ok:
                self._toast(f"{spec.name} is ready")
            elif err == downloads.CANCELLED:
                self._toast("Download cancelled. It resumes where it stopped next time.")
            else:
                self._errors[model_id] = err or "The download failed"
                self._toast(f"{spec.name} did not download: {self._errors[model_id]}")
            self._sync_models()

        try:
            if spec.runtime == "ollama":
                if self.ctx.ollama is None:
                    return
                handle = self.ctx.ollama.pull(spec.source, progress, finished)
                job = _Job(cancel=handle.cancel)
            elif spec.runtime == "openvino":
                event = threading.Event()
                downloads.jiwo_download(lambda d, t: progress(d, t), finished, event)
                job = _Job(cancel=event.set, total=downloads.total_size(downloads.JIWO_FILES))
            elif spec.runtime == "onnx":
                event = threading.Event()
                downloads.speech_download(spec.id, lambda d, t: progress(d, t), finished, event)
                job = _Job(cancel=event.set, total=downloads.total_size(downloads.SPEECH_FILES[spec.id]))
            else:
                self._toast(f"{spec.name} can't be downloaded from here yet")
                return
        except Exception as exc:
            log.exception("starting the %s download", model_id)
            self._errors[model_id] = str(exc)
            self._sync_models()
            return
        self._jobs[model_id] = job
        self._sync_models()

    def _cancel_download(self, model_id: str) -> None:
        job = self._jobs.get(model_id)
        if job is not None:
            job.status = "Cancelling"
            self._rows[model_id].show_progress(job)
            job.cancel()

    # behaviour

    def _build_behaviour(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Behaviour")

        self._policy_row = self._combo("big_model_policy", "Load the Big Model", POLICIES,
                                       "On demand frees GPU memory between requests. Always loaded answers "
                                       "sooner but keeps the GPU awake.")
        self._policy_row.add_prefix(Gtk.Image(icon_name="system-run-symbolic"))
        group.add(self._policy_row)

        self._keep_row = self._combo("ollama_keep_alive", "Keep It Loaded After a Reply", KEEP_ALIVE,
                                     "A short time saves battery, a longer one answers follow-ups faster.")
        self._keep_row.add_prefix(Gtk.Image(icon_name="preferences-system-time-symbolic"))
        group.add(self._keep_row)

        think = Adw.SwitchRow(title="Reasoning", active=bool(self.settings.get("think")),
                              subtitle="The big model thinks before it answers: better on hard requests, "
                                       "slower to start speaking.")
        think.add_prefix(Gtk.Image(icon_name="view-list-ordered-symbolic"))
        think.connect("notify::active", lambda r, _p: self._store("think", r.get_active()))
        self._watch("think", lambda: think.set_active(bool(self.settings.get("think"))))
        group.add(think)

        adj = Gtk.Adjustment(lower=0.50, upper=0.99, step_increment=0.01, page_increment=0.05,
                             value=float(self.settings.get("escalate_threshold")))
        spin = Adw.SpinRow(title="Escalation Threshold", adjustment=adj, digits=2, numeric=True,
                           subtitle="When the small model is less sure than this, the big model takes over. "
                                    "Higher sends more requests to the GPU.")
        spin.add_prefix(Gtk.Image(icon_name="go-jump-symbolic"))
        spin.connect("notify::value", lambda r, _p: self._store("escalate_threshold", round(r.get_value(), 2)))
        self._watch("escalate_threshold", lambda: spin.set_value(float(self.settings.get("escalate_threshold"))))
        group.add(spin)

        self._connect(self.settings, "changed::big_model_policy", lambda *_: self._sync_keep_row())
        self._sync_keep_row()
        return group

    def _combo(self, key: str, title: str, options, subtitle: str) -> Adw.ComboRow:
        ids = [o[0] for o in options]
        labels = [o[1] for o in options]
        current = str(self.settings.get(key))
        if current not in ids:  # a hand-edited value still shows instead of silently snapping
            ids.append(current)
            labels.append(current)
        row = Adw.ComboRow(title=title, subtitle=subtitle, use_subtitle=False,
                           model=Gtk.StringList.new(labels))
        row.set_selected(ids.index(current))
        row.connect("notify::selected", lambda r, _p: self._store(key, ids[r.get_selected()])
                    if r.get_selected() < len(ids) else None)
        self._watch(key, lambda: row.set_selected(ids.index(str(self.settings.get(key))))
                    if str(self.settings.get(key)) in ids else None)
        return row

    def _build_context_row(self) -> Adw.ComboRow:
        """How much the big model reads per request. One value for both profiles: Ollama reloads on a change."""
        ids = [n for n, _ in CONTEXT]
        current = int(self.settings.get("context_length") or 8192)
        labels = [label for _, label in CONTEXT]
        if current not in ids:
            ids.append(current)
            labels.append(f"{current} tokens")
        row = Adw.ComboRow(title="Context Length", use_subtitle=False, model=Gtk.StringList.new(labels),
                           subtitle="How much the big model reads per request: the conversation, the tools and "
                                    "a screenshot fit in 8K. More uses more GPU memory.")
        row.add_prefix(Gtk.Image(icon_name="view-paged-symbolic"))
        row.set_selected(ids.index(current))
        row.connect("notify::selected", lambda r, _p: r.get_selected() < len(ids) and
                    self._store("context_length", ids[r.get_selected()]))
        self._watch("context_length", lambda: int(self.settings.get("context_length")) in ids and
                    row.set_selected(ids.index(int(self.settings.get("context_length")))))
        return row

    def _sync_keep_row(self) -> None:
        on_demand = self.settings.get("big_model_policy") == "on_demand"
        self._keep_row.set_sensitive(on_demand)
        self._keep_row.set_tooltip_text(None if on_demand else "Always loaded keeps the big model in memory")

    # installed models

    def _build_storage(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Storage")
        self._installed_exp = Adw.ExpanderRow(title="Installed Models", use_markup=False)
        self._installed_exp.add_prefix(Gtk.Image(icon_name="drive-harddisk-symbolic"))
        group.add(self._installed_exp)
        return group

    def _sync_installed(self) -> None:
        st = self._status
        models = st.get("models") or []
        on_disk = st.get("models_source") == "disk"
        running = bool(st.get("running"))
        key = (tuple((m.get("name"), m.get("size")) for m in models), on_disk, running,
               self.settings.get("small_model"), self.settings.get("big_model"), self.ctx.ollama is None)
        if key == self._installed_key:
            return
        self._installed_key = key
        for row in self._installed_rows:
            self._installed_exp.remove(row)
        self._installed_rows.clear()

        if self.ctx.ollama is None:
            self._installed_exp.set_subtitle("Unavailable without the Ollama manager")
        elif not models:
            self._installed_exp.set_subtitle("No Ollama models yet")
        else:
            total = sum(int(m.get("size") or 0) for m in models)
            count = f"{len(models)} model{'s' if len(models) != 1 else ''}, {_size(total)}"
            self._installed_exp.set_subtitle(count + (" · read from disk while Ollama is stopped" if on_disk else ""))
        in_use = {_norm(s.source) for s in (models_catalog.get(self.settings.get("small_model")),
                                            models_catalog.get(self.settings.get("big_model")))
                  if s is not None and s.runtime == "ollama"}
        for m in models:
            name = m.get("name", "?")
            details = m.get("details") or {}
            facts = [_size(m.get("size") or 0), details.get("parameter_size"), details.get("quantization_level")]
            row = Adw.ActionRow(title=name, subtitle=" · ".join(f for f in facts if f), use_markup=False)
            if _norm(name) in in_use:
                row.add_suffix(_tag("In use", "accent"))
            delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            delete.add_css_class("flat")
            delete.add_css_class("circular")
            delete.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete {name}"])
            delete.set_sensitive(running)
            delete.set_tooltip_text(f"Delete {name}" if running else "Start Ollama to delete models")
            delete.connect("clicked", lambda *_, n=name, s=int(m.get("size") or 0): self._confirm_delete(n, s))
            row.add_suffix(delete)
            self._installed_exp.add_row(row)
            self._installed_rows.append(row)

    def _confirm_delete(self, name: str, size: int) -> None:
        body = f"This frees {_size(size)}. You can download it again later."
        picked = [models_catalog.get(self.settings.get(k)) for k in ("small_model", "big_model")]
        if any(s is not None and s.runtime == "ollama" and _norm(s.source) == _norm(name) for s in picked):
            body += "\n\nThe assistant uses this model now, so it will not answer until you download it again " \
                    "or choose another."
        self._alert(f"Delete {name}?", body, "delete", "Delete", True, lambda: self._delete(name))

    def _delete(self, name: str) -> None:
        mgr = self.ctx.ollama
        if mgr is None:
            return

        def done(ok, err):
            self._toast(f"Deleted {name}" if ok else f"Could not delete {name}: {err}")

        mgr.delete(name, done)

    # refresh and lifecycle

    def _sync(self) -> None:
        try:
            self._sync_ollama()
            self._sync_models()
            self._sync_installed()
        except Exception:
            log.exception("updating the models page")

    def _refresh(self) -> None:
        self._check_jiwo()
        mgr = self.ctx.ollama
        if mgr is not None:
            mgr.refresh()
        self._sync()

    def _on_map(self, *_):
        self._check_jiwo()
        mgr = self.ctx.ollama
        if mgr is not None and not self._polling and hasattr(mgr, "start_polling"):
            self._polling = True
            mgr.start_polling()
        self._sync()

    def _on_unmap(self, *_):
        mgr = self.ctx.ollama
        if mgr is not None and self._polling:
            self._polling = False
            mgr.stop_polling()

    def _on_destroy(self, *_):
        self._on_unmap()
        self._profiles.close()
        self.settings.close()
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
