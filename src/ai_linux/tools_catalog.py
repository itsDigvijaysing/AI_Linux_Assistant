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

# Tools page sections, in order: (title, icon, what is inside)
CATEGORIES = {
    "system": ("System Control", "preferences-system-symbolic", "Volume, brightness, Night Light, Do Not Disturb, "
               "lock, Settings and status"),
    "apps": ("Apps and Files", "view-app-grid-symbolic", "Open, switch and close apps; open and list folders"),
    "web": ("Web Access", "web-browser-symbolic", "Web search in your browser, weather and quick look-ups"),
    "media": ("Media", "multimedia-player-symbolic", "Play, pause and skip music or video"),
    "screen": ("Screen and Text", "view-reveal-symbolic", "Screenshots, reading the screen, writing and the clipboard"),
    "command": ("Command Line", "utilities-terminal-symbolic", "A terminal, and commands for anything else"),
    "assistant": ("Assistant", "user-info-symbolic", "Time, timers, its voice, sleep and what it remembers"),
    "tasks": ("Todoist", "object-select-symbolic", "Add, read and finish your Todoist tasks with your API token"),
}

# MCP server per family; the engine names tools mcp.<server>.<tool>, built-ins have no prefix.
_SERVERS = {"desktop": "skills_actions", "shell": "shell", "voice": "voice", "assistant": ""}


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str
    description: str
    family: str  # desktop | shell | voice | assistant
    gated: bool  # changes the computer, so it runs only while actions are allowed
    icon: str = "applications-system-symbolic"
    category: str = "assistant"  # a CATEGORIES key
    online: bool = False  # reaches the internet: only with Web Access on

    @property
    def qualified(self) -> str:
        server = _SERVERS.get(self.family, "")
        return f"mcp.{server}.{self.name}" if server else self.name


def _t(name, title, description, family, gated, icon, category, online=False) -> ToolInfo:
    return ToolInfo(name, title, description, family, gated, icon, category, online)


TOOLS: tuple[ToolInfo, ...] = (
    # System Control
    _t("set_volume", "Volume", "Turn the speaker volume up or down, mute it, or set an exact level.",
       "desktop", True, "audio-volume-high-symbolic", "system"),
    _t("set_screen_brightness", "Screen brightness", "Set the display brightness to a percentage.",
       "desktop", True, "display-brightness-symbolic", "system"),
    _t("toggle_night_light", "Night Light", "Turn the warm-screen blue-light filter on or off.",
       "desktop", True, "night-light-symbolic", "system"),
    _t("set_do_not_disturb", "Do Not Disturb", "Silence or restore notification banners.",
       "desktop", True, "notifications-disabled-symbolic", "system"),
    _t("lock_screen", "Lock screen", "Lock the screen. It never suspends, logs out or shuts down.",
       "desktop", True, "system-lock-screen-symbolic", "system"),
    _t("open_settings", "Open Settings", "Open GNOME Settings, optionally on a panel such as Sound or Wi-Fi.",
       "desktop", True, "preferences-system-symbolic", "system"),
    _t("system_status", "System status", "Battery, network, disk space, memory or CPU load.",
       "assistant", False, "utilities-system-monitor-symbolic", "system"),
    # Apps and Files
    _t("open_app_or_link", "Open apps and links", "Launch an app, open a file or folder, or open a website.",
       "desktop", True, "view-app-grid-symbolic", "apps"),
    _t("switch_to_app", "Switch to an app", "Bring an app that is already open to the front.",
       "desktop", True, "focus-windows-symbolic", "apps"),
    _t("close_app", "Close an app", "Ask an open app to quit. Web apps that run in the browser are left alone.",
       "desktop", True, "window-close-symbolic", "apps"),
    _t("installed_apps", "Installed apps", "Say which apps are installed, or what one of them is for.",
       "assistant", False, "view-list-symbolic", "apps"),
    _t("open_file_manager", "Open Files", "Open the file manager, optionally at Downloads, Documents and so on.",
       "desktop", True, "folder-symbolic", "apps"),
    _t("list_folder", "What's in a folder", "Say how many files a folder has and the newest ones, without opening it.",
       "assistant", False, "folder-open-symbolic", "apps"),
    # Web Access
    _t("search_web", "Web search", "Search the web, YouTube, Maps, GitHub or Wikipedia in your browser.",
       "desktop", True, "system-search-symbolic", "web"),
    _t("get_weather", "Weather", "The weather now, today or tomorrow, from Open-Meteo. No account needed.",
       "assistant", False, "weather-few-clouds-symbolic", "web", online=True),
    _t("web_lookup", "Quick look-up", "A short answer about a person, place, thing or word, from DuckDuckGo or "
       "Wikipedia.", "assistant", False, "dialog-information-symbolic", "web", online=True),
    # Media
    _t("control_media", "Media controls", "Play, pause, skip or stop whatever is playing.",
       "desktop", True, "multimedia-player-symbolic", "media"),
    # Screen and Text
    _t("take_screenshot", "Screenshot", "Save a screenshot of the whole screen or one window.",
       "desktop", True, "camera-photo-symbolic", "screen"),
    _t("describe_screen", "Describe the screen", "Look at what is on screen and describe, read or explain it.",
       "desktop", True, "view-reveal-symbolic", "screen"),
    _t("write_text", "Write a reply", "Read the screen, write the reply or text you ask for, and paste it where "
       "you are typing. It never presses Send.", "desktop", True, "document-edit-symbolic", "screen"),
    _t("clipboard", "Clipboard", "Copy text to the clipboard or read what is on it.",
       "desktop", True, "edit-paste-symbolic", "screen"),
    # Command Line
    _t("open_terminal", "Open a terminal", "Open a new terminal window.",
       "desktop", True, "utilities-terminal-symbolic", "command"),
    _t("run_command", "Run a command", "Run a shell command it writes itself, for anything without a tool. "
       "It always shows you the command first.", "shell", True, "system-run-symbolic", "command"),
    # Assistant
    _t("get_time", "Time and date", "Tell the current time, date or day of the week.",
       "assistant", False, "preferences-system-time-symbolic", "assistant"),
    _t("set_timer", "Timers and reminders", "Set a timer or a reminder; it rings with a sound, a notification and "
       "the message spoken, even after a restart.", "assistant", False, "alarm-symbolic", "assistant"),
    _t("set_voice", "Change voice", "Switch its own speaking voice when you ask.",
       "voice", False, "audio-speakers-symbolic", "assistant"),
    _t("go_to_sleep", "Go to sleep", "“That's all” or “stop”: stops listening until the wake word. Never sleeps the "
       "computer.", "assistant", False, "weather-clear-night-symbolic", "assistant"),
    # the big model's memory (engine tools.MEMORY_TOOLS); the facts are in Knowledge
    _t("remember", "Remember", "Save a short fact about you for later conversations, such as a preference "
       "or a project.", "assistant", False, "user-info-symbolic", "assistant"),
    _t("forget", "Forget", "Delete a saved fact when you ask it to forget or say it is wrong.",
       "assistant", False, "edit-clear-symbolic", "assistant"),
    # Todoist (on the menus only once a token is saved)
    _t("todoist", "Todoist", "“Add buy milk tomorrow to Todoist”, “what's on my Todoist today”, “mark buy milk as "
       "done” (it asks first). Uses the internet with your token, whatever Web Access says.",
       "assistant", False, "object-select-symbolic", "tasks"),
)

_BY_NAME = {t.name: t for t in TOOLS}


def lookup(name: str) -> ToolInfo | None:
    """Find a tool by bare name or by the engine's mcp.<server>.<tool> name."""
    if not name:
        return None
    return _BY_NAME.get(name) or _BY_NAME.get(name.rsplit(".", 1)[-1])


def for_family(family: str) -> list[ToolInfo]:
    return [t for t in TOOLS if t.family == family]


def for_category(category: str) -> list[ToolInfo]:
    return [t for t in TOOLS if t.category == category]


def title_for(name: str) -> str:
    """Human title for a tool call name, falling back to the raw name for MCP extras."""
    info = lookup(name)
    return info.title if info else name
