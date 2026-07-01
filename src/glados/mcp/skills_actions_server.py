"""Desktop skills exposed as native function-calling tools (AI_Linux).

Instead of retrieving a SKILL-*.md and injecting a command string into the user turn (which a small
model can't reliably reason about and which misfired on ambient words), each desktop capability is a
NAMED, typed tool the model selects natively. The tool list IS the model's capability list, so it is
genuinely aware of what it can do. Each tool builds the exact command from the matching SKILL-*.md and
runs it through the shared gated executor (glados.mcp.shell_exec.run_shell) — so the destructive-command
denylist and the action gate (mcp.skills_actions.* is a gated family) apply exactly as for mcp.shell.
"""

from __future__ import annotations

import json
import logging
import re
import shlex

from loguru import logger
from mcp.server.fastmcp import FastMCP

from glados.mcp.shell_exec import run_shell

logger.remove()
logging.getLogger().setLevel(logging.CRITICAL)

mcp = FastMCP("skills_actions")

_SINK = "@DEFAULT_AUDIO_SINK@"
_SETTINGS_PANELS = {"sound", "display", "wifi", "network", "bluetooth", "power", "notifications", "privacy"}
_KNOWN_FOLDERS = {
    "home": "$HOME", "downloads": "$HOME/Downloads", "documents": "$HOME/Documents",
    "pictures": "$HOME/Pictures", "desktop": "$HOME/Desktop", "music": "$HOME/Music", "videos": "$HOME/Videos",
}


def _run(command: str) -> str:
    """Execute a fixed/curated command through the shared gated executor; return its JSON result."""
    return json.dumps(run_shell(command))


@mcp.tool()
def set_screen_brightness(percent: int) -> str:
    """Set the display (screen) brightness to a percentage from 0 to 100."""
    p = max(1, min(100, int(percent)))  # avoid 0% (screen off); clamp to sane range
    return _run(f"brightnessctl set {p}%")


@mcp.tool()
def set_volume(action: str = "", percent: int = -1) -> str:
    """Change the speaker volume. Use action 'up'/'down'/'mute'/'unmute', or set an exact level with percent (0-100)."""
    if 0 <= int(percent) <= 100:
        return _run(f"wpctl set-volume {_SINK} {int(percent)}%")
    a = (action or "").strip().lower()
    cmd = {
        "up": f"wpctl set-volume {_SINK} 5%+",
        "down": f"wpctl set-volume {_SINK} 5%-",
        "mute": f"wpctl set-mute {_SINK} 1",
        "unmute": f"wpctl set-mute {_SINK} 0",
    }.get(a)
    if not cmd:
        return json.dumps({"error": "set_volume needs action up|down|mute|unmute or a percent 0-100"})
    return _run(cmd)


@mcp.tool()
def lock_or_suspend(action: str = "lock") -> str:
    """Lock the screen, suspend/sleep the laptop, or log out. action = 'lock' | 'suspend' | 'logout'."""
    a = (action or "lock").strip().lower()
    cmd = {
        "lock": "loginctl lock-session",
        "suspend": "systemctl suspend",
        "sleep": "systemctl suspend",
        "logout": "gnome-session-quit --logout --no-prompt",
    }.get(a)
    if not cmd:
        return json.dumps({"error": "lock_or_suspend needs action lock|suspend|logout"})
    return _run(cmd)


@mcp.tool()
def take_screenshot(window: str = "") -> str:
    """Take a screenshot and save it (whole screen, or a specific window by title)."""
    w = (window or "").strip()
    if w:
        return _run(f"ai-linux-screenshot --window {shlex.quote(w)}")
    return _run("ai-linux-screenshot --full")


@mcp.tool()
def open_app_or_link(target: str) -> str:
    """Open/launch an application, file, folder, or website. target = an app name, a path, or a URL."""
    t = (target or "").strip()
    if not t:
        return json.dumps({"error": "open_app_or_link needs a target (app name, path, or URL)"})
    low = t.lower()
    if "://" in t or low.startswith("www.") or re.match(r"^[\w.-]+\.(com|org|net|io|dev|gov|edu|co)(/|$)", low):
        url = t if "://" in t else "https://" + t
        return _run(f"xdg-open {shlex.quote(url)}")
    if t.startswith(("/", "~", "$HOME")):
        return _run(f"xdg-open {shlex.quote(t)}")
    if re.fullmatch(r"[\w.+-]+", t):  # a bare token -> treat as an application id (Files=org.gnome.Nautilus, etc.)
        return _run(f"gtk-launch {shlex.quote(t)}")
    return _run(f"xdg-open {shlex.quote(t)}")


@mcp.tool()
def control_media(action: str) -> str:
    """Control the currently-playing media. action = 'play'/'pause'/'next'/'previous'/'stop'."""
    a = (action or "").strip().lower()
    cmd = {
        "play": "playerctl play-pause", "pause": "playerctl play-pause", "resume": "playerctl play-pause",
        "next": "playerctl next", "previous": "playerctl previous", "back": "playerctl previous",
        "stop": "playerctl stop",
    }.get(a)
    if not cmd:
        return json.dumps({"error": "control_media needs action play|pause|next|previous|stop"})
    return _run(cmd)


@mcp.tool()
def toggle_night_light(on: bool) -> str:
    """Turn the Night Light (blue-light filter / warm screen) on or off."""
    val = "true" if on else "false"
    return _run(f"gsettings set org.gnome.settings-daemon.plugins.color night-light-enabled {val}")


@mcp.tool()
def set_do_not_disturb(on: bool) -> str:
    """Turn Do Not Disturb (silence notification banners) on or off."""
    # DND on == hide banners (show-banners false)
    val = "false" if on else "true"
    return _run(f"gsettings set org.gnome.desktop.notifications show-banners {val}")


@mcp.tool()
def open_settings(panel: str = "") -> str:
    """Open GNOME Settings, optionally a panel: sound, display, wifi, network, bluetooth, power, notifications, privacy."""
    p = (panel or "").strip().lower()
    if p in _SETTINGS_PANELS:
        return _run(f"gnome-control-center {p}")
    return _run("gnome-control-center")


@mcp.tool()
def open_terminal() -> str:
    """Open a terminal window."""
    return _run("gtk-launch org.gnome.Ptyxis")


@mcp.tool()
def open_file_manager(folder: str = "") -> str:
    """Open the file manager, optionally at a folder: home, downloads, documents, pictures, desktop, music, videos."""
    key = (folder or "").strip().lower().strip("/").split("/")[0]
    path = _KNOWN_FOLDERS.get(key, "$HOME")
    return _run(f'xdg-open "{path}"')


@mcp.tool()
def clipboard(action: str, text: str = "") -> str:
    """Use the clipboard. action = 'copy' (with text) or 'read'/'paste' (returns the current clipboard)."""
    a = (action or "").strip().lower()
    if a == "copy":
        if not text:
            return json.dumps({"error": "clipboard copy needs text"})
        return _run(f"wl-copy {shlex.quote(text)}")
    if a in ("read", "paste", "get"):
        return _run("wl-paste")
    return json.dumps({"error": "clipboard needs action copy|read"})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
