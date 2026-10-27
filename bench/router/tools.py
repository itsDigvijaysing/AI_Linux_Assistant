"""Tool menus for the router test: the 16 shipped tools, plus 10 candidate tools to test adding new ones."""

NONE = "none"
NONE_DESC = "Not a desktop command: chat, a general question, or a request none of these tools can do."

CURRENT = {
    "set_screen_brightness": "Set the display brightness to a percentage, or make the screen brighter or dimmer.",
    "set_volume": "Turn the speaker volume up or down, mute it, or set an exact level.",
    "lock_screen": "Lock the screen.",
    "take_screenshot": "Save a screenshot of the whole screen or one window to a file.",
    "open_app_or_link": "Launch an app, open a file or folder, or open a website.",
    "search_web": "Search the web, YouTube, Maps, GitHub or Wikipedia in the browser.",
    "control_media": "Play, pause, skip or stop whatever music or video is playing.",
    "toggle_night_light": "Turn the warm-screen blue-light filter (Night Light) on or off.",
    "set_do_not_disturb": "Silence or restore notification banners (Do Not Disturb).",
    "open_settings": "Open the system Settings app, optionally on a panel such as Sound, Wi-Fi or Display.",
    "open_terminal": "Open a new terminal window.",
    "open_file_manager": "Open the file manager, optionally at Downloads, Documents and so on.",
    "clipboard": "Copy text to the clipboard or read what is on it.",
    "run_command": "Run a shell command for a computer task no other tool covers.",
    "set_voice": "Change the assistant's own speaking voice.",
    "go_to_sleep": "Stop the assistant listening until the wake word is said again.",
}

NEW = {
    "get_time": "Tell the current time, date or day of the week.",
    "system_status": "Report this computer's battery level, network, disk space, memory or CPU load.",
    "set_timer": "Set a timer, alarm or reminder for a time or after a duration.",
    "describe_screen": "Look at what is on the screen right now and describe, read or explain it.",
    "switch_window": "Switch to, minimize, maximize or close an open app window.",
    "type_text": "Type the dictated text into the app that has focus.",
    "toggle_wireless": "Turn Wi-Fi, Bluetooth or airplane mode on or off.",
    "remember": "Remember a fact or note for later, or recall something it was told before.",
    "get_weather": "Tell the weather or forecast for a place.",
    "find_file": "Find a file or folder on this computer by name or type.",
}


def menu(with_new: bool) -> dict:
    m = dict(CURRENT)
    if with_new:
        m.update(NEW)
    m[NONE] = NONE_DESC
    return m


def gold_for(label: str, with_new: bool) -> str:
    """A request for a tool that isn't on the menu should go to 'none'."""
    return label if (label in CURRENT or label == NONE or with_new) else NONE
