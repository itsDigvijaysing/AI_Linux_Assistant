"""Development helpers: render windows and widgets to PNG (works inside a headless compositor)."""

from __future__ import annotations

import logging
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

log = logging.getLogger(__name__)


def render_to_png(widget: Gtk.Widget, path: str) -> bool:
    w, h = widget.get_width(), widget.get_height()
    if w <= 0 or h <= 0:
        log.error("widget not allocated yet")
        return False
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        return False
    texture = widget.get_native().get_renderer().render_texture(node, None)
    return texture.save_to_png(path)


def screenshot(app, path: str, page: str | None, state: str | None, delay_ms: int = 900) -> None:
    """Show the window on ``page`` in ``state``, save a PNG, then quit."""
    app.hold()
    win = app.get_window()
    w, _, h = os.environ.get("AILX_SHOT_SIZE", "1100x720").partition("x")  # taller shows a whole page
    win.set_default_size(int(w), int(h))
    if page and hasattr(win, "show_page"):
        win.show_page(page)  # before present(): the window is not mapped yet, so the swap is safe
    win.present()
    if state:
        app.ctx.bus.set_state(state)

    tries = {"left": 3}

    def shoot():
        ok = render_to_png(win, path)
        if not ok and tries["left"] > 0:
            tries["left"] -= 1  # a pending relayout or stalled frame clock leaves no node yet; try again
            win.queue_draw()
            GLib.timeout_add(700, shoot)
            return False
        log.info("screenshot %s -> %s", "saved" if ok else "FAILED", path)
        app.release()
        app.quit()
        return False

    GLib.timeout_add(delay_ms, shoot)
