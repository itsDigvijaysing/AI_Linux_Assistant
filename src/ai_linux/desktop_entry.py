"""App-grid launcher: a .desktop entry plus the app icons under ~/.local/share, user-level, no sudo.

    python3 -m ai_linux.desktop_entry install | uninstall
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ai_linux.autostart import _quote, launcher
from ai_linux.paths import APP_NAME, ASSETS_DIR, ICON_NAME

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICONS = (f"hicolor/scalable/apps/{ICON_NAME}.svg", f"hicolor/symbolic/apps/{ICON_NAME}-symbolic.svg",
         *(f"hicolor/{s}x{s}/apps/{ICON_NAME}.png" for s in SIZES))


def _data_home() -> Path:
    # Read at call time so tests and headless runs can point it elsewhere.
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def entry_path() -> Path:
    # Named after the app id so GNOME ties the running window to this launcher.
    return _data_home() / "applications" / f"{ICON_NAME}.desktop"


def entry() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "GenericName=Voice Assistant",
        "Comment=Local-first voice assistant that talks and runs simple desktop tasks",
        f"Exec={_quote(launcher())}",
        f"Icon={ICON_NAME}",
        "Terminal=false",
        "StartupNotify=true",
        "Categories=Utility;Accessibility;",
        "Keywords=voice;assistant;speech;phoenix;",
    ]) + "\n"


def installed_paths() -> list[Path]:
    icons = _data_home() / "icons"
    return [entry_path()] + [icons / rel for rel in ICONS]


def install() -> list[Path]:
    written = []
    target = entry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(entry(), encoding="utf-8")
    written.append(target)
    for rel in ICONS:
        dest = _data_home() / "icons" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ASSETS_DIR / "icons" / rel).read_bytes())
        written.append(dest)
    return written


def uninstall() -> list[Path]:
    removed = []
    for path in installed_paths():
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def main(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    if action not in ("install", "uninstall"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for path in (install if action == "install" else uninstall)():
        print(("wrote " if action == "install" else "removed ") + str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
