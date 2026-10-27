"""What the assistant can do: the built-in tools shown on the Tools and Safety pages.

Mirrors the engine's tools in src/ai_linux/engine/tools.py (tests/test_tools_catalog.py keeps the names
in sync). No GTK, safe to import anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

FAMILIES = {
    "desktop": "Desktop actions",
    "shell": "Shell commands",
    "voice": "Voice",
    "assistant": "Assistant",
}

# MCP server per family; the engine names tools mcp.<server>.<tool>, built-ins have no prefix.
_SERVERS = {"desktop": "skills_actions", "shell": "shell", "voice": "voice", "assistant": ""}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str
    description: str
    family: str  # desktop | shell | voice | assistant
    gated: bool  # runs only while actions are armed
    icon: str = "applications-system-symbolic"

    @property
    def qualified(self) -> str:
        server = _SERVERS.get(self.family, "")
        return f"mcp.{server}.{self.name}" if server else self.name


TOOLS: tuple[ToolInfo, ...] = (
    ToolInfo("set_screen_brightness", "Screen brightness", "Set the display brightness to a percentage.",
             "desktop", True, "display-brightness-symbolic"),
    ToolInfo("set_volume", "Volume", "Turn the speaker volume up or down, mute it, or set an exact level.",
             "desktop", True, "audio-volume-high-symbolic"),
    ToolInfo("lock_screen", "Lock screen", "Lock the screen. It never suspends, logs out or shuts down.",
             "desktop", True, "system-lock-screen-symbolic"),
    ToolInfo("take_screenshot", "Screenshot", "Save a screenshot of the whole screen or one window.",
             "desktop", True, "camera-photo-symbolic"),
    ToolInfo("open_app_or_link", "Open apps and links", "Launch an app, open a file or folder, or open a website.",
             "desktop", True, "view-app-grid-symbolic"),
    ToolInfo("search_web", "Web search", "Search the web, YouTube, Maps, GitHub or Wikipedia in your browser.",
             "desktop", True, "system-search-symbolic"),
    ToolInfo("control_media", "Media controls", "Play, pause, skip or stop whatever is playing.",
             "desktop", True, "multimedia-player-symbolic"),
    ToolInfo("toggle_night_light", "Night Light", "Turn the warm-screen blue-light filter on or off.",
             "desktop", True, "night-light-symbolic"),
    ToolInfo("set_do_not_disturb", "Do Not Disturb", "Silence or restore notification banners.",
             "desktop", True, "notifications-disabled-symbolic"),
    ToolInfo("open_settings", "Open Settings", "Open GNOME Settings, optionally on a panel such as Sound or Wi-Fi.",
             "desktop", True, "preferences-system-symbolic"),
    ToolInfo("open_terminal", "Open a terminal", "Open a new terminal window.",
             "desktop", True, "utilities-terminal-symbolic"),
    ToolInfo("open_file_manager", "Open Files", "Open the file manager, optionally at Downloads, Documents and so on.",
             "desktop", True, "folder-symbolic"),
    ToolInfo("clipboard", "Clipboard", "Copy text to the clipboard or read what is on it.",
             "desktop", True, "edit-paste-symbolic"),
    ToolInfo("describe_screen", "Describe the screen", "Look at what is on screen and describe, read or explain it.",
             "desktop", True, "view-reveal-symbolic"),
    ToolInfo("write_text", "Write a reply", "Read the screen, write the reply or text you ask for, and paste it where "
             "you are typing. It never presses Send.", "desktop", True, "document-edit-symbolic"),
    ToolInfo("run_command", "Run a command", "Run a shell command it writes itself, for anything without a tool.",
             "shell", True, "system-run-symbolic"),
    ToolInfo("set_voice", "Change voice", "Switch its own speaking voice when you ask.",
             "voice", False, "audio-speakers-symbolic"),
    ToolInfo("go_to_sleep", "Go to sleep", "Stop listening for follow-ups until the wake word. Never sleeps the computer.",
             "assistant", False, "weather-clear-night-symbolic"),
    ToolInfo("get_time", "Time and date", "Tell the current time, date or day of the week.",
             "assistant", False, "preferences-system-time-symbolic"),
    ToolInfo("system_status", "System status", "Battery, network, disk space, memory or CPU load.",
             "assistant", False, "utilities-system-monitor-symbolic"),
)

_BY_NAME = {t.name: t for t in TOOLS}


def lookup(name: str) -> ToolInfo | None:
    """Find a tool by bare name or by the engine's mcp.<server>.<tool> name."""
    if not name:
        return None
    return _BY_NAME.get(name) or _BY_NAME.get(name.rsplit(".", 1)[-1])


def for_family(family: str) -> list[ToolInfo]:
    return [t for t in TOOLS if t.family == family]


def title_for(name: str) -> str:
    """Human title for a tool call name, falling back to the raw name for MCP extras."""
    info = lookup(name)
    return info.title if info else name
