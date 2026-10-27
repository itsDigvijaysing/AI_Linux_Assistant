"""Main window: a navigation sidebar and lazily built content pages."""

from __future__ import annotations

import importlib
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from ai_linux.config import APP_NAME  # noqa: E402
from ai_linux.state import AssistantState  # noqa: E402
from ai_linux.ui import bar_model  # noqa: E402

log = logging.getLogger(__name__)

# name, title, icon (all verified in /usr/share/icons/Adwaita/symbolic)
PAGES = (
    ("home", "Home", "go-home-symbolic"),
    ("history", "History", "document-open-recent-symbolic"),
    ("models", "Models", "applications-science-symbolic"),
    ("voice", "Voice", "audio-input-microphone-symbolic"),
    ("tools", "Tools", "applications-utilities-symbolic"),
    ("safety", "Safety", "security-high-symbolic"),
    ("health", "Health", "computer-symbolic"),
)
FOOTER = (
    ("app.preferences", "Preferences", "preferences-system-symbolic"),
    ("app.about", "About AI Linux", "help-about-symbolic"),
)
PAGE_NAMES = tuple(p[0] for p in PAGES)
_TITLES = {name: title for name, title, _icon in PAGES}
_ACCELS_SET: set[int] = set()  # accels live on the app, so register them once per app


class _BarsMark(Gtk.DrawingArea):
    """The brand's 11-bar taper, drawn once in the accent blue."""

    BAR_W, GAP, MIN_H, MAX_H = 2.0, 1.6, 4.0, 16.0

    def __init__(self):
        width = int(bar_model.N_BARS * self.BAR_W + (bar_model.N_BARS - 1) * self.GAP + 1)
        super().__init__(content_width=width, content_height=int(self.MAX_H), valign=Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], [APP_NAME])

    def _draw(self, _area, cr, _width, height):
        cr.set_source_rgb(*bar_model.hex_to_rgb("#1c71d8"))
        r = self.BAR_W / 2
        for i, taper in enumerate(bar_model.TAPER):
            h = self.MIN_H + (self.MAX_H - self.MIN_H) * (taper - 0.3) / 0.7
            x, y = i * (self.BAR_W + self.GAP), (height - h) / 2
            cr.new_sub_path()
            cr.arc(x + r, y + r, r, 3.14159, 0)
            cr.arc(x + r, y + h - r, r, 0, 3.14159)
            cr.close_path()
        cr.fill()


def _nav_row(title: str, icon: str) -> Gtk.ListBoxRow:
    box = Gtk.Box(spacing=12)
    box.append(Gtk.Image(icon_name=icon))
    box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
    row = Gtk.ListBoxRow(child=box)
    row.update_property([Gtk.AccessibleProperty.LABEL], [title])
    return row


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, ctx):
        super().__init__(application=ctx.app, title=APP_NAME, default_width=1040, default_height=720)
        self.ctx = ctx
        self.set_size_request(360, 360)
        self._pages: dict[str, Adw.NavigationPage] = {}
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self._current: str | None = None
        self._selecting = False  # true while show_page moves the sidebar selection itself
        self._handlers: list[tuple[object, int]] = []
        self._banners: list[Adw.Banner] = []  # one per page: moving one banner between pages froze painting
        self._banner_text = ""
        self._banner_source: str | None = None  # "state" | "notice"

        self._split = Adw.NavigationSplitView(min_sidebar_width=210, max_sidebar_width=260,
                                              sidebar_width_fraction=0.24)
        self._split.set_sidebar(self._build_sidebar())

        self._toasts = Adw.ToastOverlay(child=self._split)
        self.set_content(self._toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self._split, "collapsed", True)
        self.add_breakpoint(bp)

        self._add_actions()
        self._connect(ctx.bus, "state-changed", lambda _b, s, d: self._on_state(s, d))
        self._connect(ctx.bus, "notice", lambda _b, level, text: self._on_notice(level, text))
        self._connect(ctx.settings, "changed::keep_running", lambda *_: self._sync_hide_on_close())
        self._sync_hide_on_close()
        self.connect("destroy", self._on_destroy)

        self.show_page("home")
        self._on_state(ctx.bus.state.value, ctx.bus.detail)

    # construction

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        brand = Gtk.Box(spacing=9)
        brand.append(_BarsMark())
        brand.append(Gtk.Label(label=APP_NAME, css_classes=["ail-sidebar-title"]))
        header.set_title_widget(brand)

        self._nav = Gtk.ListBox()
        self._nav.add_css_class("navigation-sidebar")
        self._nav.update_property([Gtk.AccessibleProperty.LABEL], ["Pages"])
        for name, title, icon in PAGES:
            row = _nav_row(title, icon)
            row.page_name = name
            self._rows[name] = row
            self._nav.append(row)
        self._nav.connect("row-selected", self._on_row_selected)
        self._nav.connect("row-activated", lambda _l, row: self.show_page(row.page_name))

        footer = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        footer.add_css_class("navigation-sidebar")
        footer.add_css_class("ail-sidebar-footer")
        for action, title, icon in FOOTER:
            row = _nav_row(title, icon)
            row.action = action
            footer.append(row)
        footer.connect("row-activated", lambda _l, row: self.activate_action(row.action, None))

        scroller = Gtk.ScrolledWindow(child=self._nav, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        view.add_bottom_bar(footer)
        return Adw.NavigationPage(title=APP_NAME, tag="sidebar", child=view)

    def _add_actions(self) -> None:
        page = Gio.SimpleAction.new("show-page", GLib.VariantType.new("s"))
        page.connect("activate", lambda _a, value: self.show_page(value.get_string()))
        self.add_action(page)
        search = Gio.SimpleAction.new("search", None)
        search.connect("activate", lambda *_: self._focus_search())
        self.add_action(search)
        app = self.ctx.app
        if app is None or id(app) in _ACCELS_SET:
            return
        _ACCELS_SET.add(id(app))
        for i, name in enumerate(PAGE_NAMES, start=1):
            app.set_accels_for_action(f"win.show-page('{name}')", [f"<Control>{i}"])
        app.set_accels_for_action("win.search", ["<Control>f"])

    def _connect(self, obj, signal: str, cb) -> None:
        if obj is not None:
            self._handlers.append((obj, obj.connect(signal, cb)))

    # navigation

    def show_page(self, name: str) -> None:
        """Show a page by name and reveal it when the split view is collapsed.

        Call it before present() or once the window is up: a swap in between crashes GTK's Wayland IM context.
        """
        name = (name or "").strip().lower()
        if name not in _TITLES:
            log.warning("unknown page %r", name)
            return
        if name != self._current:
            page = self._page(name)
            self._split.set_content(page)
            self._current = name
        self._select_row(name)
        self._split.set_show_content(True)

    @property
    def current_page(self) -> str | None:
        return self._current

    def add_toast(self, toast: Adw.Toast) -> None:
        self._toasts.add_toast(toast)

    def _page(self, name: str) -> Adw.NavigationPage:
        page = self._pages.get(name)
        if page is not None:
            return page
        try:
            page = importlib.import_module(f"ai_linux.ui.pages.{name}").Page(self.ctx)
        except Exception as exc:
            log.exception("page %s failed to load", name)
            return self._error_page(name, exc)  # not cached, so the next visit tries again
        self._add_banner(page)
        self._pages[name] = page
        return page

    def _error_page(self, name: str, exc: Exception) -> Adw.NavigationPage:
        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda *_: self._retry(name))
        status = Adw.StatusPage(
            icon_name="dialog-error-symbolic", title=f"{_TITLES[name]} Could Not Load",
            description=GLib.markup_escape_text(f"{type(exc).__name__}: {exc}\n\nDetails are in the app log."),
            child=retry,
        )
        view = Adw.ToolbarView(content=status)
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.NavigationPage(title=_TITLES[name], tag=name, child=view)
        self._add_banner(page)
        return page

    def _retry(self, name: str) -> None:
        if self._current == name:
            self._current = None  # forces show_page to rebuild instead of keeping the error page
        self.show_page(name)

    def _select_row(self, name: str) -> None:
        row = self._rows.get(name)
        if row is None or self._nav.get_selected_row() is row:
            return
        self._selecting = True
        try:
            self._nav.select_row(row)
        finally:
            self._selecting = False

    def _on_row_selected(self, _list, row) -> None:
        # keyboard arrows select without activating: follow them, but don't leave the sidebar when collapsed
        if row is None or self._selecting:
            return
        if self._split.get_collapsed():
            return
        self.show_page(row.page_name)

    def _focus_search(self) -> None:
        page = self._pages.get(self._current or "")
        if page is not None and hasattr(page, "focus_search"):
            page.focus_search()

    # banner and toasts

    def _add_banner(self, page: Adw.NavigationPage) -> None:
        """Give the page its own banner under its header bar, as the HIG places banners."""
        toolbar = page.get_child()
        if not isinstance(toolbar, Adw.ToolbarView):
            return
        banner = Adw.Banner(button_label="Dismiss", use_markup=False, title=self._banner_text,
                            revealed=self._banner_source is not None)
        banner.connect("button-clicked", lambda *_: self._hide_banner())
        toolbar.add_top_bar(banner)
        self._banners = [b for b in self._banners if b.get_parent() is not None] + [banner]

    def _show_banner(self, text: str, source: str) -> None:
        self._banner_text, self._banner_source = text, source
        for banner in self._banners:
            banner.set_title(text)
            banner.set_revealed(True)

    def _hide_banner(self) -> None:
        self._banner_source = None
        for banner in self._banners:
            banner.set_revealed(False)

    def _on_state(self, state: str, detail: str) -> None:
        if state == AssistantState.ERROR.value:
            self._show_banner(detail or "The assistant stopped because of an error. Details are in the app log.",
                              "state")
        elif self._banner_source == "state":
            self._hide_banner()

    def _on_notice(self, level: str, text: str) -> None:
        if level == "error":
            self._show_banner(text, "notice")
            return
        toast = Adw.Toast(title=text, timeout=5 if level == "warning" else 3)
        toast.set_use_markup(False)
        self._toasts.add_toast(toast)

    # lifecycle

    def _sync_hide_on_close(self) -> None:
        self.set_hide_on_close(bool(self.ctx.settings.get("keep_running")))

    def _on_destroy(self, *_):
        for obj, hid in self._handlers:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._handlers.clear()
