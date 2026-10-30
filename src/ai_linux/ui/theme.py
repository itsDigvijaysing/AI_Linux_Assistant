"""Stylesheets, icons and light/dark handling."""

from __future__ import annotations

import logging

from gi.repository import Adw, Gdk, Gtk

from ai_linux.config import ASSETS_DIR

log = logging.getLogger(__name__)
CSS_DIR = ASSETS_DIR.parent / "ui" / "css"
_loaded_displays: set[int] = set()


def install(display: Gdk.Display | None = None) -> None:
    """Load every ui/css/*.css (alphabetical, base.css first) and our icon path for a display.

    The pill lives on a second (X11) display, so call this once per display.
    """
    display = display or Gdk.Display.get_default()
    if display is None or hash(display) in _loaded_displays:
        return
    _loaded_displays.add(hash(display))
    files = sorted(CSS_DIR.glob("*.css"), key=lambda p: (p.name != "base.css", p.name))
    for path in files:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(str(path))
        except Exception as exc:  # a broken sheet must not take the UI down
            log.warning("css %s: %s", path.name, exc)
            continue
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Gtk.IconTheme.get_for_display(display).add_search_path(str(ASSETS_DIR / "icons"))


def color_scheme(theme: str) -> Adw.ColorScheme:
    return {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(theme,
                                                                                         Adw.ColorScheme.DEFAULT)


def apply_color_scheme(settings, display: Gdk.Display | None = None) -> None:
    manager = Adw.StyleManager.get_for_display(display) if display else Adw.StyleManager.get_default()

    def update(*_):
        manager.set_color_scheme(color_scheme(settings.get("theme")))

    update()
    settings.connect("changed::theme", update)
