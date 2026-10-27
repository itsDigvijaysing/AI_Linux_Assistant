"""Start at login through an XDG autostart entry that launches the app hidden in the tray."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from ai_linux.config import APP_NAME, ICON_NAME, REPO_ROOT

log = logging.getLogger(__name__)


def _config_home() -> Path:
    # Read at call time so tests and headless runs can point it elsewhere.
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _system_dirs() -> list[Path]:
    dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return [Path(d) / "autostart" for d in dirs.split(":") if d]


def desktop_path() -> Path:
    # ICON_NAME stays fixed even when tests override APP_ID, so the entry keeps one stable name.
    return _config_home() / "autostart" / f"{ICON_NAME}.desktop"


def launcher() -> str:
    return shutil.which("ai-linux-app") or str(REPO_ROOT / "bin" / "ai-linux-app")


def _quote(arg: str) -> str:
    """Quote one Exec argument per the Desktop Entry spec."""
    arg = arg.replace("%", "%%")
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    for c in ("\\", '"', "`", "$"):
        arg = arg.replace(c, "\\" + c)
    # The key-file layer unescapes backslashes once more before Exec parsing.
    return '"' + arg.replace("\\", "\\\\") + '"'


def _entry(hidden: bool = False) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Local-first voice assistant, started in the background",
        f"Exec={_quote(launcher())} --background",
        f"Icon={ICON_NAME}",
        "Terminal=false",
        "StartupNotify=false",
        "NoDisplay=false",
        f"X-GNOME-Autostart-enabled={'false' if hidden else 'true'}",
    ]
    if hidden:
        lines.append("Hidden=true")
    return "\n".join(lines) + "\n"


def _read_entry(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values, in_group = {}, False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_group = line == "[Desktop Entry]"
        elif in_group and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def _entry_enabled(values: dict[str, str]) -> bool:
    return (values.get("Hidden", "false").lower() != "true"
            and values.get("X-GNOME-Autostart-enabled", "true").lower() != "false")


def is_enabled() -> bool:
    """The user entry wins; a system-wide one counts only when no user entry overrides it."""
    values = _read_entry(desktop_path())
    if values is not None:
        return _entry_enabled(values)
    for d in _system_dirs():
        sys_values = _read_entry(d / desktop_path().name)
        if sys_values is not None:
            return _entry_enabled(sys_values)
    return False


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai-linux.", suffix=".desktop")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_enabled(on: bool) -> None:
    """Write or remove the entry. Raises OSError so the caller can tell the user it failed."""
    path = desktop_path()
    if on:
        _write_atomic(path, _entry())
        log.info("autostart on: %s", path)
        return
    # A system-wide entry can only be switched off with a Hidden=true override.
    if any((d / path.name).exists() for d in _system_dirs()):
        _write_atomic(path, _entry(hidden=True))
    else:
        path.unlink(missing_ok=True)
    log.info("autostart off: %s", path)
