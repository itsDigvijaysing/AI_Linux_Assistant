"""What the assistant can do.

Each tool has a one-line description (the router menu and the big model's tool list), a JSON schema, an
optional code parser for simple phrasings ("volume to 30"), an optional choice the decision model can fill
("which settings page?"), and a short spoken reply, so a typed tool never needs a second LLM call.
Everything that acts goes through the gated executor (denylist + arm gate + confirmation).
"""

from __future__ import annotations

import datetime
import os
import re
import shlex
import shutil
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_linux.engine import apps as appdb
from ai_linux.engine import todoist, web
from ai_linux.engine.timers import spoken_span
from ai_linux.paths import REPO_ROOT, STATE_DIR
from ai_linux.safety.gate import ConfirmRequest

SINK = "@DEFAULT_AUDIO_SINK@"
NONE = "none"
NONE_DESC = "Not a desktop command: chat, a general question, or a request none of these tools can do."


@dataclass
class Result:
    ok: bool
    say: str
    output: str = ""
    summarise: bool = False  # the big model turns `output` into the spoken reply
    speak: bool | None = None  # True: always say it, even when finished actions only get a sound
    cue: str = ""  # the sound instead of "ok" / "fail"


@dataclass
class Env:
    """What tools may use. Built by the engine; tests pass fakes."""

    executor: Any
    confirm: Callable | None = None
    set_voice: Callable[[str], bool] | None = None
    sleep: Callable[[], None] | None = None
    vision: Callable[[str], str] | None = None
    compose: Callable[[str], str] | None = None
    cue: Callable[[str], None] | None = None  # plays a sound from engine/sounds.py
    knowledge: Any = None  # ai_linux.knowledge.Knowledge
    apps: Callable[[], list] = appdb.installed_cached
    setting: Callable[[str], Any] = lambda key: None
    timers: Any = None  # ai_linux.engine.timers.Timers
    running: Callable[[str], bool] = appdb.running  # is a program with this file name running
    on_bus: Callable[[str], bool] = appdb.on_bus  # does a running app own this D-Bus name
    secret: Callable[[str], str | None] = lambda name: None  # tokens the app read from the user's keyring


@dataclass(frozen=True)
class Choice:
    """An argument the decision model fills by picking from options (name -> description)."""

    arg: str
    question: str
    options: Callable[[str, Env], dict[str, str]]
    convert: Callable[[str], Any] = lambda x: x


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict
    run: Callable[[dict, Env], Result]
    parse: Callable[[str, Env], dict | None] | None = None
    choice: Choice | None = None
    family: str = "desktop"  # desktop | shell | voice | assistant | info
    required: tuple[str, ...] = ()
    needs_args: bool = True
    answers: bool = False  # its reply is the information asked for, so it is always spoken
    gate_id: str = ""  # MCP tools: mcp.<server>.<tool>

    @property
    def speaks(self) -> bool:
        return self.answers or self.family == "info"

    @property
    def gate_name(self) -> str:
        return {"desktop": f"desktop.{self.name}", "shell": "shell.run_command",
                "mcp": self.gate_id}.get(self.family, self.name)

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": list(self.required)}}}


# shared helpers

def _result(r: dict, say: str, fail: str) -> Result:
    if r.get("refused") or r.get("denied"):
        return Result(False, "I'm not allowed to do that right now." if r.get("denied") else
                      "I won't do that, it could damage your system.", r.get("error", ""))
    if "error" in r or r.get("returncode", 0) != 0:
        return Result(False, fail, r.get("error") or r.get("stderr", ""))
    return Result(True, say, r.get("stdout", ""))


def _argv(env: Env, tool: str, argv: list[str], say: str, fail: str, args: dict | None = None,
          detach: bool = False) -> Result:
    if detach:
        argv = ["setsid", "-f", *argv]
    r = env.executor.run_argv(argv, tool_name=f"desktop.{tool}", args=args or {}, confirm=env.confirm,
                              resource_caps=not detach)
    return _result(r, say, fail)


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


def number(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    words = re.findall(r"[a-z]+", text.lower())
    for i, w in enumerate(words):
        if w == "hundred":
            return 100
        if w in _TENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            return _TENS[w] + (_UNITS[nxt] if nxt in _UNITS and _UNITS[nxt] < 10 else 0)
        if w in _UNITS and w not in ("one",):
            return _UNITS[w]
    return None


def _has(text: str, *words: str) -> bool:
    return bool(re.search(r"\b(" + "|".join(words) + r")\b", text))


def _on_off(text: str) -> bool | None:
    if _has(text, "off", "disable", "stop", "deactivate", "no more"):
        return False
    if _has(text, "on", "enable", "start", "activate"):
        return True
    return None


ON_OFF = {"on": "turn it on", "off": "turn it off"}

# brightness and volume


def _p_level(text: str) -> dict | None:
    t = text.lower()
    n = number(t)
    if _has(t, "max", "maximum", "full", "highest"):
        return {"percent": 100}
    if _has(t, "minimum", "lowest"):
        return {"percent": 5}
    if _has(t, "half", "halfway"):
        return {"percent": 50}
    if _has(t, "quarter"):
        return {"percent": 25}
    step = n if (n is not None and _has(t, "by")) else None
    if n is not None and step is None and 0 <= n <= 100:
        return {"percent": n}
    if _has(t, "up", "brighter", "louder", "increase", "raise", "higher", "more", "turn up", "boost"):
        return {"action": "up", **({"step": step} if step else {})}
    if _has(t, "down", "dim", "dimmer", "darker", "quieter", "lower", "decrease", "softer", "less", "reduce"):
        return {"action": "down", **({"step": step} if step else {})}
    return None


def _p_volume(text: str, env: Env) -> dict | None:
    t = text.lower()
    if _has(t, "unmute"):
        return {"action": "unmute"}
    if _has(t, "mute", "silence") and number(t) is None:
        return {"action": "mute"}
    return _p_level(t)


def _clamp(n, lo=0, hi=100) -> int:
    return max(lo, min(hi, int(n)))


def _level(value) -> int | None:
    """A level the big model filled: 40, 55.6, "40", "40%", "fifty"; None when it isn't a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%?\s*", value)
        return int(float(m.group(1))) if m else number(value)
    return None


def run_brightness(a: dict, env: Env) -> Result:
    level = _level(a.get("percent"))
    if level is not None:
        p = _clamp(level, 1)
        return _argv(env, "set_screen_brightness", ["brightnessctl", "-q", "set", f"{p}%"],
                     f"Brightness {p} percent.", "I couldn't change the brightness.", a)
    step = _clamp(_level(a.get("step")) or 20, 1)
    change = {"up": f"+{step}%", "down": f"{step}%-"}.get(str(a.get("action", "")).lower())
    if not change:
        return Result(False, "How bright should the screen be?")
    return _argv(env, "set_screen_brightness", ["brightnessctl", "-q", "set", change],
                 "Brighter." if change.startswith("+") else "Dimmer.", "I couldn't change the brightness.", a)


def run_volume(a: dict, env: Env) -> Result:
    level = _level(a.get("percent"))
    if level is not None and level >= 0:
        p = _clamp(level)
        return _argv(env, "set_volume", ["wpctl", "set-volume", SINK, f"{p}%"], f"Volume {p} percent.",
                     "I couldn't change the volume.", a)
    act = str(a.get("action", "")).lower()
    step = _clamp(_level(a.get("step")) or 10, 1)
    argv = {"up": ["wpctl", "set-volume", "-l", "1.0", SINK, f"{step}%+"], "down": ["wpctl", "set-volume", SINK, f"{step}%-"],
            "mute": ["wpctl", "set-mute", SINK, "1"], "unmute": ["wpctl", "set-mute", SINK, "0"]}.get(act)
    if not argv:
        return Result(False, "Louder, quieter, or a number?")
    say = {"up": "Volume up.", "down": "Volume down.", "mute": "Muted.", "unmute": "Unmuted."}[act]
    return _argv(env, "set_volume", argv, say, "I couldn't change the volume.", a)


LEVEL_CHOICE = {"up": "make it higher, louder or brighter", "down": "make it lower, quieter or dimmer",
                "exact": "set it to a particular level, such as a number, half or a fraction"}
LEVEL_ONLY = lambda pick: None if pick == "exact" else pick  # noqa: E731  an exact level goes to the big model
VOLUME_CHOICE = {**LEVEL_CHOICE, "mute": "mute the sound", "unmute": "unmute the sound"}

# simple actions


def run_lock(a: dict, env: Env) -> Result:
    return _argv(env, "lock_screen", ["loginctl", "lock-session"], "Locking the screen.", "I couldn't lock the screen.")


def _screenshot_bin() -> str | None:
    return shutil.which("ai-linux-screenshot") or (
        str(REPO_ROOT / "bin/ai-linux-screenshot") if (REPO_ROOT / "bin/ai-linux-screenshot").exists() else None)


def run_screenshot(a: dict, env: Env) -> Result:
    exe = _screenshot_bin()
    if not exe:
        return Result(False, "The screenshot helper isn't installed.")
    w = str(a.get("window") or "").strip()
    argv = [exe, "--window", w] if w else [exe, "--full"]
    return _argv(env, "take_screenshot", argv, "Screenshot saved in Pictures.", "I couldn't take a screenshot.", a)


SETTINGS_PANELS = {"sound": "sound and audio devices", "display": "screen, resolution and scaling",
                   "wifi": "Wi-Fi networks", "network": "wired network and VPN", "bluetooth": "Bluetooth devices",
                   "power": "battery and power saving", "notifications": "notifications",
                   "privacy": "privacy and security", "background": "wallpaper and appearance",
                   "mouse": "mouse and touchpad", "keyboard": "keyboard and shortcuts",
                   "displays": "screen, resolution and scaling", "system": "date, users, updates and about"}
_PANEL_ALIASES = {"wi fi": "wifi", "wi-fi": "wifi", "audio": "sound", "screen": "display", "battery": "power",
                  "wallpaper": "background", "appearance": "background", "touchpad": "mouse", "date": "system"}


def _p_settings(text: str, env: Env) -> dict | None:
    t = text.lower()
    for k, v in _PANEL_ALIASES.items():
        if k in t:
            return {"panel": v}
    for k in SETTINGS_PANELS:
        if _has(t, k):
            return {"panel": k}
    if re.search(r"\bsettings\b\s*$", t) or _has(t, "open settings", "system settings"):
        return {"panel": ""}
    return None


def run_settings(a: dict, env: Env) -> Result:
    p = str(a.get("panel") or "").strip().lower()
    p = _PANEL_ALIASES.get(p, "display" if p == "displays" else p)
    argv = ["gnome-control-center", p] if p in SETTINGS_PANELS else ["gnome-control-center"]
    return _argv(env, "open_settings", argv, "Opening Settings.", "I couldn't open Settings.", a, detach=True)


def run_terminal(a: dict, env: Env) -> Result:
    for app_id in ("org.gnome.Ptyxis", "org.gnome.Terminal", "org.gnome.Console"):
        if any(x.id == app_id for x in env.apps()):
            return _argv(env, "open_terminal", ["gtk-launch", app_id], "Opening a terminal.",
                         "I couldn't open a terminal.", detach=True)
    return Result(False, "I couldn't find a terminal app.")


FOLDERS = {"home": "", "downloads": "Downloads", "documents": "Documents", "pictures": "Pictures",
           "desktop": "Desktop", "music": "Music", "videos": "Videos"}


def _folder_path(name: str) -> Path:
    from subprocess import run as _run
    key = name.lower()
    xdg = {"downloads": "DOWNLOAD", "documents": "DOCUMENTS", "pictures": "PICTURES", "desktop": "DESKTOP",
           "music": "MUSIC", "videos": "VIDEOS"}.get(key)
    if xdg and shutil.which("xdg-user-dir"):
        try:
            out = _run(["xdg-user-dir", xdg], capture_output=True, text=True, timeout=2).stdout.strip()
            if out:
                return Path(out)
        except Exception:
            pass
    return Path.home() / FOLDERS.get(key, "")


def _p_files(text: str, env: Env) -> dict | None:
    t = text.lower()
    for k in FOLDERS:
        if _has(t, k, k.rstrip("s")):
            return {"folder": k}
    return {"folder": "home"} if _has(t, "files", "file manager", "nautilus", "folder", "folders") else None


def run_files(a: dict, env: Env) -> Result:
    key = str(a.get("folder") or "home").lower().strip("/").split("/")[0]
    path = _folder_path(key if key in FOLDERS else "home")
    where = "" if key in ("", "home") else f" {key.capitalize()}"
    return _argv(env, "open_file_manager", ["xdg-open", str(path)], f"Opening{where or ' your files'}.",
                 "I couldn't open the file manager.", a, detach=True)


MEDIA = {"play": "play or resume what is paused", "pause": "pause what is playing",
         "next": "skip to the next track", "previous": "go back to the previous track", "stop": "stop playback"}


def _p_media(text: str, env: Env) -> dict | None:
    t = text.lower()
    for words, act in ((("next", "skip"), "next"), (("previous", "back", "last song", "last track"), "previous"),
                       (("pause", "hold"), "pause"), (("stop",), "stop"), (("play", "resume", "continue"), "play")):
        if _has(t, *words):
            return {"action": act}
    return None


def run_media(a: dict, env: Env) -> Result:
    act = str(a.get("action", "")).lower()
    act = {"resume": "play", "skip": "next", "back": "previous"}.get(act, act)
    cmd = {"play": "play", "pause": "pause", "next": "next", "previous": "previous", "stop": "stop"}.get(act)
    if not cmd:
        return Result(False, "Play, pause, next or previous?")
    say = {"play": "Playing.", "pause": "Paused.", "next": "Next track.", "previous": "Previous track.", "stop": "Stopped."}
    return _argv(env, "control_media", ["playerctl", cmd], say[cmd], "Nothing is playing.", a)


def _p_toggle(text: str, env: Env) -> dict | None:
    v = _on_off(text.lower())
    return None if v is None else {"on": v}


def _p_dnd(text: str, env: Env) -> dict | None:
    t = text.lower()
    if _has(t, "silence", "mute", "quiet", "block", "hide", "stop notifications", "no notifications"):
        return {"on": True}
    if _has(t, "show", "restore", "allow", "unmute", "resume", "bring back"):
        return {"on": False}
    return _p_toggle(t, env)


def _gs(env: Env, tool: str, schema: str, key: str, value: str, say: str, a: dict) -> Result:
    return _argv(env, tool, ["gsettings", "set", schema, key, value], say, "I couldn't change that setting.", a)


def run_night_light(a: dict, env: Env) -> Result:
    on = bool(a.get("on", True))
    return _gs(env, "toggle_night_light", "org.gnome.settings-daemon.plugins.color", "night-light-enabled",
               "true" if on else "false", f"Night Light {'on' if on else 'off'}.", a)


def run_dnd(a: dict, env: Env) -> Result:
    on = bool(a.get("on", True))
    return _gs(env, "set_do_not_disturb", "org.gnome.desktop.notifications", "show-banners",
               "false" if on else "true", f"Do Not Disturb {'on' if on else 'off'}.", a)


def _p_clipboard(text: str, env: Env) -> dict | None:
    t = text.strip()
    m = re.match(r"(?i)^(?:please\s+)?copy\s+(.+?)(?:\s+to\s+(?:the\s+|my\s+)?clipboard)?[.!]?$", t)
    if m and m.group(1).lower() not in ("that", "this", "it"):
        return {"action": "copy", "text": m.group(1)}
    if _has(t.lower(), "read", "what's on", "what is on", "paste", "show"):
        return {"action": "read"}
    return None


def run_clipboard(a: dict, env: Env) -> Result:
    act = str(a.get("action", "")).lower()
    if act == "copy":
        text = str(a.get("text") or "")
        if not text:
            return Result(False, "What should I copy?")
        return _argv(env, "clipboard", ["wl-copy", "--", text], "Copied.", "I couldn't copy that.", a)
    if act in ("read", "paste", "get"):
        r = env.executor.run_argv(["wl-paste", "--no-newline"], tool_name="desktop.clipboard", args=a,
                                  confirm=env.confirm)
        res = _result(r, "", "The clipboard is empty.")
        if res.ok:
            text = res.output.strip()
            res.say = f"Your clipboard says: {text[:300]}" if text else "The clipboard is empty."
            res.speak = True
        return res
    return Result(False, "Copy or read the clipboard?")


# opening things


def _is_url(s: str) -> bool:
    low = s.lower().replace(" dot ", ".")
    return "://" in s or low.startswith("www.") or bool(
        re.match(r"^[\w.-]+\.(com|org|net|io|dev|gov|edu|co|ai|app|tv|in|uk)(/\S*)?$", low))


_OPEN_RE = re.compile(r"(?i)^(?:hey\s+)?(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?"
                      r"(?:open|launch|start|run|bring up|fire up|load)\s+(?:up\s+)?(?:the\s+|my\s+|a\s+)?")
_TRAIL_RE = re.compile(r"(?i)\s+(?:app|application|program|for me|please|now|again|window)\s*$")


def open_target(text: str) -> str | None:
    m = _OPEN_RE.match(text.strip())
    if not m:
        return None
    t = text.strip()[m.end():].strip(" .!?")
    for _ in range(3):
        t = _TRAIL_RE.sub("", t)
    return t or None


def _p_open(text: str, env: Env) -> dict | None:
    target = open_target(text)
    if target is None:
        return None
    if _is_url(target) or target.startswith(("/", "~")):
        return {"target": target}
    app, score = appdb.match(target, env.apps())
    return {"target": app.id} if app and score >= 0.8 else None


def _app_options(text: str, env: Env) -> dict[str, str]:
    name = open_target(text) or text
    return {a.id: a.name for a in appdb.candidates(name, env.apps())}


def run_open(a: dict, env: Env) -> Result:
    t = str(a.get("target") or "").strip()
    if not t:
        return Result(False, "What should I open?")
    if _is_url(t):
        url = t.replace(" dot ", ".")
        url = url if "://" in url else "https://" + url
        return _argv(env, "open_app_or_link", ["xdg-open", url], "Opening the website.", "I couldn't open that link.",
                     a, detach=True)
    if t.startswith(("/", "~")):
        p = os.path.expanduser(t)
        if not os.path.exists(p):
            return Result(False, "That file or folder doesn't exist.")
        return _argv(env, "open_app_or_link", ["xdg-open", p], f"Opening {Path(p).name or p}.",
                     "I couldn't open that.", a, detach=True)
    if t.lower() in FOLDERS:
        return run_files({"folder": t.lower()}, env)
    apps = env.apps()
    app = next((x for x in apps if x.id == t.removesuffix(".desktop")), None)
    if app is None:
        app, score = appdb.match(t, apps)
        if app is None or score < 0.8:
            return Result(False, f"I couldn't find an app called {t}.")
    return _argv(env, "open_app_or_link", ["gtk-launch", app.id], f"Opening {app.name}.",
                 f"I couldn't open {app.name}.", a, detach=True)


SITES = {"web": "https://duckduckgo.com/?q=", "google": "https://www.google.com/search?q=",
         "youtube": "https://www.youtube.com/results?search_query=", "maps": "https://www.google.com/maps/search/",
         "github": "https://github.com/search?q=", "wikipedia": "https://en.wikipedia.org/w/index.php?search="}
_SITE_RE = "youtube|google maps|maps|github|wikipedia|google|the web|web"


def _p_search(text: str, env: Env) -> dict | None:
    t = text.strip().rstrip(".!?")
    low = t.lower()
    site = "web"
    m = re.search(rf"(?i)\b(?:on|in|at|using)\s+(?:the\s+)?({_SITE_RE})\s*$", t)
    if m:
        site = m.group(1).lower()
        t = t[:m.start()].strip()
    q = re.sub(r"(?i)^(?:please\s+)?(?:can you\s+)?(?:search|look up|google|look for|find|show me)\s+"
               r"(?:the web\s+|online\s+)?(?:for\s+)?", "", t)
    m2 = re.match(rf"(?i)^({_SITE_RE})\s+(?:for\s+)?", q)
    if m2 and site == "web":
        site = m2.group(1).lower()
        q = q[m2.end():]
    if q == t and not low.startswith(("search", "google", "look up", "look for", "find", "show me")):
        return None
    site = {"the web": "web", "google maps": "maps"}.get(site, site)
    return {"query": q.strip(), "site": site} if q.strip() else None


def run_search(a: dict, env: Env) -> Result:
    q = str(a.get("query") or "").strip()
    if not q:
        return Result(False, "What should I search for?")
    site = str(a.get("site") or "web").lower()
    base = SITES.get(site, SITES["web"])
    where = "" if site in ("web", "google") else f" on {site.capitalize()}"
    return _argv(env, "search_web", ["xdg-open", base + urllib.parse.quote(q)], f"Searching{where} for {q}.",
                 "I couldn't open the browser.", a, detach=True)


# shell, voice, assistant, info


def run_command(a: dict, env: Env) -> Result:
    cmd = str(a.get("command") or "").strip()
    r = env.executor.run_shell(cmd, tool_name="shell.run_command", args=a, confirm=env.confirm)
    res = _result(r, "Done.", "That command failed.")
    if not (r.get("refused") or r.get("denied")):
        out = (r.get("stdout") or "") + (("\n" + r["stderr"]) if r.get("stderr") else "")
        res.output = f"exit code {r.get('returncode', '?')}\n{out.strip()}" if "error" not in r else r["error"]
        res.summarise = True
    return res


VOICES = {"supertonic:M1": "male voice 1", "supertonic:M3": "male voice 3", "supertonic:M4": "male voice 4",
          "supertonic:M5": "male voice 5", "supertonic:F1": "female voice 1 (default)", "supertonic:F2": "female voice 2",
          "supertonic:F3": "female voice 3", "supertonic:F4": "female voice 4", "supertonic:F5": "female voice 5"}


def _p_voice(text: str, env: Env) -> dict | None:
    t = text.lower()
    m = re.search(r"\b(male|female)\s+(?:voice\s+)?(?:number\s+)?(\d)\b", t)
    if m:
        v = f"supertonic:{m.group(1)[0].upper()}{m.group(2)}"
        return {"voice": v} if v in VOICES else None
    if _has(t, "female", "woman", "girl", "lady"):
        return {"voice": "supertonic:F1"}
    if _has(t, "male", "man", "guy", "boy"):
        return {"voice": "supertonic:M1"}
    return None


def run_voice(a: dict, env: Env) -> Result:
    v = str(a.get("voice") or "")
    if v not in VOICES or env.set_voice is None:
        return Result(False, "I don't have that voice.")
    return Result(True, "Done, how does this sound?") if env.set_voice(v) else Result(False, "I couldn't switch voices.")


def run_sleep(a: dict, env: Env) -> Result:
    if env.sleep:
        env.sleep()
    return Result(True, "Okay, going to sleep.", cue="sleep")


def run_time(a: dict, env: Env) -> Result:
    now = datetime.datetime.now()
    return Result(True, f"It's {now.strftime('%-I:%M %p')}, {now.strftime('%A %-d %B')}.")


def _battery() -> str:
    for d in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            cap = (d / "capacity").read_text().strip()
            st = (d / "status").read_text().strip().lower()
            return f"Battery {cap} percent, {st}."
        except OSError:
            continue
    return "No battery found."


def _memory() -> str:
    info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
    total = int(info["MemTotal"].split()[0]) / 1048576
    avail = int(info["MemAvailable"].split()[0]) / 1048576
    return f"{total - avail:.1f} of {total:.0f} gigabytes of memory in use."


def _disk() -> str:
    du = shutil.disk_usage(Path.home())
    return f"{du.free / 1e9:.0f} gigabytes free on your home drive."


def _cpu() -> str:
    load = os.getloadavg()[0]
    return f"CPU load is {load:.1f} across {os.cpu_count()} threads."


def _network() -> str:
    up = [p.parent.name for p in Path("/sys/class/net").glob("*/operstate")
          if p.parent.name != "lo" and p.read_text().strip() == "up"]
    return "Connected to the network." if up else "No network connection."


STATUS = {"battery": ("battery level and charging", _battery), "memory": ("memory (RAM) use", _memory),
          "disk": ("free disk space", _disk), "cpu": ("CPU load", _cpu), "network": ("network connection", _network)}


def _p_status(text: str, env: Env) -> dict | None:
    t = text.lower()
    hits = [k for k, words in (("battery", ("battery", "charge", "charging", "power")),
                               ("memory", ("memory", "ram")), ("disk", ("disk", "storage", "space")),
                               ("cpu", ("cpu", "processor", "load")),
                               ("network", ("network", "internet", "wifi", "wi-fi", "online", "connected")))
            if _has(t, *words)]
    return {"aspect": hits[0]} if len(hits) == 1 else None


def run_status(a: dict, env: Env) -> Result:
    key = str(a.get("aspect") or "").lower()
    if key in STATUS:
        return Result(True, STATUS[key][1]())
    return Result(True, " ".join(fn() for _, fn in STATUS.values() if fn is not _network))


def run_describe(a: dict, env: Env) -> Result:
    if env.vision is None:
        return Result(False, "Looking at the screen needs the big model with vision.")
    q = str(a.get("question") or "Describe what is on the screen.")
    try:
        answer = env.vision(q)
    except Exception as exc:
        return Result(False, "I couldn't look at the screen.", str(exc))
    return Result(True, answer)


def _p_describe(text: str, env: Env) -> dict:
    return {"question": text}


PORTAL_HELPER = Path(__file__).resolve().parents[1] / "paste_portal.py"
PORTAL_TOKEN = STATE_DIR / "portal-paste-token"
SYSTEM_PYTHON = "/usr/bin/python3"  # the helper needs python3-gi, which the engine's venv doesn't have
YDOTOOL_KEYS = {  # evdev codes: 29 left Ctrl, 42 left Shift, 47 V
    "ctrl-v": ["29:1", "47:1", "47:0", "29:0"],
    "ctrl-shift-v": ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
}
PORTAL_DIALOG_S, PORTAL_TIMED_OUT = 90, 4  # match paste_portal.py
_portal = {"broken": False, "denied": False}  # for this engine run, like OpenWhispr's portalFailed / portalDenied


def _ydotoold_running() -> bool:
    sock = os.environ.get("YDOTOOL_SOCKET")
    return any(os.path.exists(p) for p in ([sock] if sock else
                                          [f"/run/user/{os.getuid()}/.ydotool_socket", "/tmp/.ydotool_socket"]))


def _press_paste(run: Callable[[list[str]], dict]) -> str | None:
    """Presses the paste keys in OpenWhispr's GNOME order; the method used, or None if nothing could.
    Portal with a saved token (silent), then ydotool, then the portal with its one-time permission dialog."""
    ok = lambda res: "error" not in res and res.get("returncode", 0) == 0  # noqa: E731
    helper = [SYSTEM_PYTHON, str(PORTAL_HELPER)]
    portal_ready = os.path.exists(SYSTEM_PYTHON) and PORTAL_HELPER.is_file()

    def portal() -> bool:
        first = not PORTAL_TOKEN.is_file()  # the permission dialog waits for a person
        res = run(helper + ["paste", "--token-file", str(PORTAL_TOKEN)], timeout=PORTAL_DIALOG_S + 5 if first else 20)
        code = res.get("returncode")
        if not ok(res) and code != PORTAL_TIMED_OUT:  # a slow answer to the dialog is not a broken portal
            _portal["denied" if code in (2, 3) else "broken"] = True
        return ok(res)

    usable = portal_ready and not _portal["broken"] and not _portal["denied"]
    if usable and PORTAL_TOKEN.is_file() and portal():
        return "portal"
    if _ydotoold_running():
        terminal = portal_ready and run(helper + ["detect"]).get("stdout", "").strip() == "terminal"
        if ok(run(["ydotool", "key", *YDOTOOL_KEYS["ctrl-shift-v" if terminal else "ctrl-v"]])):
            return "ydotool"
    if usable and not PORTAL_TOKEN.is_file() and portal():
        return "portal"
    return None


def paste_text(env: Env, text: str, args: dict) -> Result:
    """Puts text where the user's cursor is: clipboard + the paste keys (Ctrl+Shift+V in terminals), then gives
    the old clipboard back. Never presses Enter, so nothing is sent."""
    def run(argv: list[str], timeout: float = 20) -> dict:
        return env.executor.run_argv(argv, tool_name="desktop.write_text", args=args, confirm=env.confirm,
                                     resource_caps=False, timeout=timeout)  # wl-copy keeps serving the clipboard
    old = run(["wl-paste", "--no-newline", "--type", "text/plain"])
    put = _result(run(["wl-copy", "--", text]), "", "I couldn't use the clipboard.")
    if not put.ok:
        return Result(False, put.say, put.output)
    time.sleep(0.15)
    if _press_paste(run) is None:
        return Result(True, "It's on your clipboard: press Control V to paste it.", text, speak=True)
    if "error" not in old and old.get("returncode") == 0 and old.get("stdout"):
        previous = old["stdout"]
        threading.Timer(1.5, lambda: run(["wl-copy", "--", previous])).start()
    return Result(True, "Done, I pasted it.", text)


def run_write(a: dict, env: Env) -> Result:
    if env.compose is None:
        return Result(False, "Writing text needs the big model.")
    request = str(a.get("request") or "").strip()
    try:
        draft = env.compose(request)
    except Exception as exc:
        return Result(False, "I couldn't write that.", str(exc))
    if not draft:
        return Result(False, "I couldn't write that.")
    return paste_text(env, draft, a)


def _p_write(text: str, env: Env) -> dict:
    return {"request": text}


def _p_none(text: str, env: Env) -> dict:
    return {}


def _choice(arg: str, question: str, options: dict[str, str], convert=lambda x: x) -> Choice:
    return Choice(arg, question, lambda text, env: options, convert)


def run_remember(args: dict, env: Env) -> Result:
    if env.knowledge is None:
        return Result(False, "I can't save facts right now.")
    fact, why = env.knowledge.add(str(args.get("fact") or ""), kept=bool(args.get("always")), source="learned")
    if fact is None:
        return Result(False, why)
    return Result(True, "I'll always remember that." if fact.kept else "I'll remember that.")


def run_forget(args: dict, env: Env) -> Result:
    if env.knowledge is None:
        return Result(False, "I can't change saved facts right now.")
    gone = env.knowledge.forget(str(args.get("fact") or ""))
    return Result(bool(gone), "Okay, I've forgotten that." if gone else
                  "I didn't find exactly that. You can delete facts in Knowledge too.")


# apps and folders, read only

def _p_apps(text: str, env: Env) -> dict | None:
    m = re.search(r"(?i)\bwhat (?:does|is|can) (.+?) (?:do|for|used for|app)\b", text)
    if m:
        app, score = appdb.match(m.group(1), env.apps())
        return {"app": app.id} if app and score >= 0.8 else None
    return {} if _has(text.lower(), "apps", "applications", "programs", "software") else None


def run_apps(a: dict, env: Env) -> Result:
    apps = env.apps()
    want = str(a.get("app") or "").strip()
    if not want:
        lines = [f"{x.name}: {x.comment}" if x.comment else x.name for x in apps]
        return Result(True, appdb.summary(apps) + " Ask me what one is for, or say open and its name.",
                      "\n".join(lines))
    app = _find_app(want, env)
    if app is None:
        return Result(False, f"I couldn't find an app called {want}.")
    if not app.comment:
        return Result(True, f"{app.name} is installed, but it doesn't describe itself.")
    return Result(True, f"{app.name}: {app.comment.rstrip('.')}.")


_LIST_RE = re.compile(r"(?i)\b(?:what'?s|what is|what are|list|show|which files are|anything)\b.*\b(?:in|inside)\b")


def _p_list_folder(text: str, env: Env) -> dict | None:
    if not _LIST_RE.search(text):
        return None
    t = text.lower()
    return next(({"folder": k} for k in FOLDERS if k != "home" and _has(t, k, k.rstrip("s"))), None)


def _mtime(path: Path) -> float:
    try:
        return path.lstat().st_mtime  # lstat: a broken link must not fail the whole folder
    except OSError:
        return 0.0


def run_list_folder(a: dict, env: Env) -> Result:
    key = str(a.get("folder") or "home").lower()
    key = key if key in FOLDERS else "home"
    path = _folder_path(key)
    try:
        entries = [x for x in path.iterdir() if not x.name.startswith(".")]
    except OSError:
        return Result(False, f"I can't read your {key} folder.")
    items = sorted(entries, key=_mtime, reverse=True)
    name = "Your home folder" if key == "home" else key.capitalize()
    if not items:
        return Result(True, f"{name} is empty.")
    newest = ", ".join(x.stem[:40] for x in items[:4])
    n = len(items)
    return Result(True, f"{name} has {n} item{'s' * (n != 1)}. The newest: {newest}.",
                  "\n".join(x.name for x in items[:200]))


# switching and closing apps

def _find_app(name: str, env: Env):
    apps = env.apps()
    app = next((x for x in apps if x.id == name), None)
    if app is None and name:
        app, score = appdb.match(name, apps)
        app = app if app is not None and score >= 0.8 else None
    return app


def _app_target(text: str, *verbs: str) -> str | None:
    m = re.search(rf"(?i)\b(?:{'|'.join(verbs)})\s+(?:to\s+)?(?:the\s+)?(.+?)(?:\s+(?:app|window|for me|please))*[.!]*$",
                  text)
    return m.group(1).strip() if m else None


def _object_path(app_id: str) -> str:
    """GApplication's object path for an id: dots become slashes, dashes underscores, a leading digit gets "_"."""
    parts = [re.sub(r"[^A-Za-z0-9_]", "_", x) for x in app_id.split(".")]
    return "/" + "/".join("_" + x if x[:1].isdigit() else x for x in parts)


def _activate_argv(app_id: str, *method: str) -> list[str]:
    return ["gdbus", "call", "--session", "--dest", app_id, "--object-path", _object_path(app_id), "--method", *method]


def _p_switch(text: str, env: Env) -> dict | None:
    name = _app_target(text, "switch", "go back", "go", "bring up", "show me", "focus", "jump")
    app = _find_app(name, env) if name else None
    return {"app": app.id} if app else None


def run_switch(a: dict, env: Env) -> Result:
    app = _find_app(str(a.get("app") or ""), env)
    if app is None:
        return Result(False, f"I couldn't find an app called {a.get('app') or 'that'}.")
    if env.on_bus(app.id):  # a running GNOME app raises its own window
        return _argv(env, "switch_to_app", _activate_argv(app.id, "org.freedesktop.Application.Activate", "{}"),
                     f"Switching to {app.name}.", f"I couldn't switch to {app.name}.", {"app": app.id})
    if app.exe and app.exe != "flatpak" and not app.pwa and not env.running(app.exe):
        return _argv(env, "switch_to_app", ["gtk-launch", app.id], f"{app.name} wasn't open, so I'm opening it.",
                     f"I couldn't open {app.name}.", {"app": app.id}, detach=True)
    # Launching it again would open a second window; GNOME gives other apps no way to raise one.
    return Result(False, f"I can't bring {app.name} to the front from here. Click it in the dock or press Super "
                         "and Tab.")


def _p_close(text: str, env: Env) -> dict | None:
    name = _app_target(text, "close", "quit", "exit", "kill")
    app = _find_app(name, env) if name else None
    return {"app": app.id} if app else None


def run_close(a: dict, env: Env) -> Result:
    app = _find_app(str(a.get("app") or ""), env)
    if app is None:
        return Result(False, f"I couldn't find an app called {a.get('app') or 'that'}.")
    if app.pwa:
        return Result(False, f"{app.name} runs inside your browser, so close its window instead.")
    if env.on_bus(app.id):  # asks the app to quit, so it can save or ask first
        argv = _activate_argv(app.id, "org.freedesktop.Application.ActivateAction", "quit", "[]", "{}")
    elif app.exe == "flatpak" or not app.exe:  # flatpak kill is a hard kill; a launcher hides the real program
        return Result(False, f"I can't ask {app.name} to quit safely, so close its window instead.")
    elif env.running(app.exe):
        argv = ["pkill", "-TERM", "-x", "-u", str(os.getuid()), app.exe[:15]]  # the polite signal: it may save
    else:
        return Result(False, f"{app.name} isn't open.")
    return _argv(env, "close_app", argv, f"Closing {app.name}.", f"I couldn't close {app.name}.", {"app": app.id})


# timers and reminders

_UNIT_S = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
_NUM_WORD = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-]+(?:one|two|three|four|five|six|"
             r"seven|eight|nine))?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
             r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|an?|a couple of|a few)")
_SPAN_RE = re.compile(rf"(?i)\b(\d+(?:\.\d+)?|{_NUM_WORD})(\s+and\s+a\s+half)?\s+(second|sec|minute|min|hour|hr)s?"
                      r"(\s+and\s+a\s+half)?\b")
_SPAN_GAP = re.compile(r"(?i)^[\s,]*(?:and\s+)?$")  # "1 hour and 30 minutes" is one length, not two


def _num_word(n: str) -> float:
    n = re.sub(r"[\s-]+", " ", n.lower()).strip()
    if n[0].isdigit():
        return float(n)
    if n in ("a", "an"):
        return 1
    if n in ("a couple of",):
        return 2
    if n == "a few":
        return 3
    parts = n.split()
    return _TENS.get(parts[0], _UNITS.get(parts[0], 0)) + (_UNITS.get(parts[1], 0) if len(parts) > 1 else 0)


def span_seconds(text: str) -> int | None:
    """The first length said: '1 hour and 30 minutes' -> 5400, 'twenty five minutes' -> 1500,
    'an hour and a half' -> 5400, 'half an hour' -> 1800; None when no length is said."""
    half = re.search(r"(?i)\bhalf an? (hour|minute)\b", text)
    rest = text[:half.start()] + " " * (half.end() - half.start()) + text[half.end():] if half else text
    total, last_end = (_UNIT_S[half.group(1).lower()] // 2, half.end()) if half else (0, None)
    for m in _SPAN_RE.finditer(rest):
        if last_end is not None and not _SPAN_GAP.match(rest[last_end:m.start()]):
            break  # a second, separate length (inside the reminder text) is not part of this one
        value = _num_word(m.group(1)) + (0.5 if m.group(2) or m.group(4) else 0)
        total += int(value * _UNIT_S[m.group(3).lower()])
        last_end = m.end()
    return total or None


def _clock(text: str) -> tuple[str, bool] | None:
    """("HH:MM", exact): exact is False for a bare "at 5", which means the next 5 o'clock."""
    m = re.search(r"(?i)\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?!\w)", text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    if not (h < 24 and mi < 60):
        return None
    return f"{h:02d}:{mi:02d}", bool(ampm) or h == 0 or h > 12


_REMIND_RE = re.compile(r"(?i)\bremind me\s+(?:to|that|about)\s+(.+?)(?:\s+(?:in|at|after)\s+(?:\d|an?\b|one|two|"
                        r"three|four|five|ten|fifteen|twenty|thirty|half).*)?[.!]*$")
_REMIND_AFTER_RE = re.compile(r"(?i)\bremind me\b.*?\b(?:minutes?|hours?|seconds?|[ap]\.?m\.?|\d)\s+(?:to|that|about)"
                              r"\s+(.+?)[.!]*$")
_TIMER_WORDS = r"(?:timers?|reminders?|alarms?)"
_SET_START = re.compile(r"(?i)^(?:please\s+|can you\s+|could you\s+)*(?:set|start|create|make|add|put|remind me|"
                        r"wake me)\b")
_CANCEL_RE = re.compile(rf"(?i)\b(?:cancel|stop|delete|remove|clear|turn off|end)\b(?:\s+\S+){{0,5}}?\s+{_TIMER_WORDS}\b")
_LIST_TIMERS = re.compile(rf"(?i)\b(?:how long|how much time|time left|left on|what|which|any|list|show)\b.*"
                          rf"\b{_TIMER_WORDS}\b|\b{_TIMER_WORDS}\b.*\b(?:left|pending|set)\b")


def _p_timer(text: str, env: Env) -> dict | None:
    if not _SET_START.search(text):  # "remind me in 10 minutes to stop the oven" sets one, whatever it mentions
        if _CANCEL_RE.search(text):
            return {"action": "cancel", "which": text}
        if _LIST_TIMERS.search(text):
            return {"action": "list"}
    m = _REMIND_RE.search(text) or _REMIND_AFTER_RE.search(text)
    message = m.group(1).strip() if m else ""
    bare = text.replace(message, " ") if message else text  # lengths inside the message aren't the timer's
    seconds, at = span_seconds(bare), _clock(bare)
    if not seconds and not at:
        return None
    args: dict = {"action": "set", "message": message}
    if at:
        args["at"], args["exact"] = at
    else:
        args["seconds"] = seconds
    return args


def _which_timers(which: str, items: list[dict]) -> list[dict]:
    """The pending timers a cancel request means: all, the one with that length, or the one about that."""
    t = which.lower()
    if len(items) <= 1 or re.search(r"\b(?:all|every|everything|timers|reminders|alarms)\b", t):
        return items
    seconds = span_seconds(which)
    if seconds:
        return [it for it in items if it.get("length") == seconds]
    said = set(re.findall(r"[a-z']{3,}", t)) - {"cancel", "stop", "delete", "remove", "clear", "the", "timer",
                                                 "reminder", "alarm", "for", "about", "please", "you", "can", "turn"}
    return [it for it in items if said & set(re.findall(r"[a-z']{3,}", str(it.get("message") or "").lower()))]


def run_timer(a: dict, env: Env) -> Result:
    if env.timers is None:
        return Result(False, "Timers aren't available right now.")
    action = str(a.get("action") or "set")
    if action == "cancel":
        items = env.timers.pending()
        if not items:
            return Result(True, "There are no timers to cancel.")
        chosen = _which_timers(str(a.get("which") or "all"), items)
        if not chosen:
            return Result(False, f"You have {len(items)} timers. Say which one, or say cancel all timers.")
        n = env.timers.cancel([it["id"] for it in chosen] if len(chosen) < len(items) else None)
        return Result(True, f"Cancelled {n} timer{'s' * (n != 1)}.")
    if action == "list":
        items = env.timers.pending()
        if not items:
            return Result(True, "You have no timers.")
        now = time.time()
        parts = [f"{it['message'] or 'a timer'} in {spoken_span(it['due'] - now)}" for it in items[:3]]
        return Result(True, "Coming up: " + "; ".join(parts) + ".")
    message = str(a.get("message") or "").strip().rstrip(".")
    if a.get("at"):
        try:
            h, mi = (int(x) for x in str(a["at"]).split(":")[:2])
            now = datetime.datetime.now()
            due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        except (TypeError, ValueError):
            return Result(False, "I didn't get the time for that.")
        if due <= now and a.get("exact") is False and h < 12 and due + datetime.timedelta(hours=12) > now:
            due += datetime.timedelta(hours=12)  # "at 5" said at noon means 5 in the evening
        if due <= now:
            due += datetime.timedelta(days=1)
        seconds = (due - now).total_seconds()
        when = "at " + due.strftime("%-I:%M %p")
    else:
        try:
            seconds = float(a.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            return Result(False, "How long should the timer be?")
        when = "in " + spoken_span(seconds)
    env.timers.add(seconds, message)
    if message:
        return Result(True, f"Okay, I'll remind you to {message} {when}.")
    return Result(True, f"Timer set for {spoken_span(seconds)}." if not a.get("at") else f"Timer set for {when[3:]}.")


# web access (off until the user turns it on)

WEB_OFF = "Web access is off. You can turn it on in Tools, under Web Access."
WEB_TOOLS = frozenset({"get_weather", "web_lookup"})  # on the menus only while Web Access is on
_WHEN = {"today": "today", "tomorrow": "tomorrow", "tonight": "today"}
_TIME_WORDS = {"today", "tomorrow", "tonight", "now", "morning", "evening", "afternoon", "night", "week", "weekend",
               "hour", "hours", "minute", "minutes", "later", "moment", "while", "day", "days"}


def _p_weather(text: str, env: Env) -> dict | None:
    t = text.lower()
    if not _has(t, "weather", "temperature", "forecast", "rain", "raining", "sunny", "hot", "cold", "degrees",
                "umbrella", "snow"):
        return None
    when = next((v for k, v in _WHEN.items() if _has(t, k)), "now")
    m = re.search(r"(?i)\b(?:in|at|for)\s+([A-Za-z][A-Za-z .'-]{1,40}?)(?:\s+(?:today|tomorrow|tonight|now|right "
                  r"now|this week))?[?.!]*$", text)
    city = m.group(1).strip() if m else ""
    if set(city.lower().split()) & _TIME_WORDS:  # "in the evening", "for tomorrow": a time, not a place
        city = ""
    return {"city": city, "when": when}


def run_weather(a: dict, env: Env) -> Result:
    if not env.setting("web_access"):
        return Result(False, WEB_OFF)
    city = str(a.get("city") or "").strip() or str(env.setting("weather_city") or "").strip()
    if not city:
        return Result(False, "Which city? You can set your home city in Tools, under Web Access.")
    try:
        return Result(True, web.weather(city, str(a.get("when") or "now")))
    except ValueError:
        return Result(False, f"I couldn't find a place called {city}.")
    except Exception as exc:
        return Result(False, "I couldn't reach the weather service.", str(exc))


_LOOKUP_RE = re.compile(r"(?i)^(?:please\s+)?(?:can you\s+)?(?:tell me\s+)?(?:who is|who was|who are|what is|what's|"
                        r"what are|what was|define|definition of|meaning of|tell me about|look up|quickly look up)\s+")


def _p_lookup(text: str, env: Env) -> dict | None:
    q = _LOOKUP_RE.sub("", text.strip()).rstrip("?.! ")
    q = re.sub(r"(?i)\s+(?:online|on the web|on the internet)$", "", q)
    return {"query": q} if q and q != text.strip().rstrip("?.! ") else None


def run_lookup(a: dict, env: Env) -> Result:
    if not env.setting("web_access"):
        return Result(False, WEB_OFF)
    q = str(a.get("query") or "").strip()
    if not q:
        return Result(False, "What should I look up?")
    try:
        answer = web.lookup(q)
    except Exception as exc:
        return Result(False, "I couldn't reach the web.", str(exc))
    return Result(True, answer) if answer else Result(False, f"I couldn't find a short answer about {q}.")


# Todoist (shown on the menus only once the user saved a token)

TODOIST_OFF = "Todoist isn't connected. You can add your API token in Tools, under Todoist."
_TD_LIST = r"(?:todoist|to-?do\s+list|task\s+list)"
_TD_TAIL = rf"(?:\s+(?:in|on|from)\s+(?:my\s+|the\s+)?{_TD_LIST})?[.!?]*$"
_TD_ASK = r"(?i)^(?:please\s+)?(?:can you\s+|could you\s+)?"
_TD_DONE = [
    re.compile(_TD_ASK + r"mark\s+(?:the\s+task\s+)?(.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)" + _TD_TAIL),
    re.compile(_TD_ASK + r"(?:tick|check|cross)\s+off\s+(?:the\s+task\s+)?(.+?)" + _TD_TAIL),
    re.compile(_TD_ASK + r"(?:complete|finish|close)\s+(?:the\s+|my\s+)?(?:todoist\s+)?task\s+(.+?)" + _TD_TAIL),
    re.compile(_TD_ASK + rf"(?:complete|finish)\s+(.+?)\s+(?:in|on)\s+(?:my\s+)?{_TD_LIST}[.!?]*$"),
]
_TD_ADD = re.compile(_TD_ASK + r"(?:add|put|create|make|write down|note down)\s+(.+?)\s+(?:to|on|in|into|onto)\s+"
                     rf"(?:my\s+|the\s+)?{_TD_LIST}\b\s*(.*?)[.!?]*$")
_TD_NEW = re.compile(_TD_ASK + r"(?:add|create|make|new)\s+(?:a\s+)?(?:new\s+)?(?:todoist\s+)?task\s+"
                     r"(?:to\s+|called\s+|named\s+|for\s+)?(.+?)[.!?]*$")
_TD_ASKS = re.compile(r"(?i)\b(?:what|what's|whats|which|show|list|read|any|tell me|do i have|are there)\b")
_TD_ITEMS = re.compile(r"(?i)\b(?:todoist|to-?do(?:\s+list)?|tasks?)\b")


def _p_todoist(text: str, env: Env) -> dict | None:
    """Spoken Todoist requests; the words to add go to Quick Add as said (it reads the date itself)."""
    t = " ".join(text.split())
    for pat in _TD_DONE:
        m = pat.match(t)
        if m:
            return {"task": m.group(1).strip(), "action": "complete"}
    m = _TD_ADD.match(t) or _TD_NEW.match(t)
    if m:
        words = " ".join(g.strip() for g in m.groups() if g and g.strip())
        return {"text": words, "action": "add"} if words else None
    if _TD_ASKS.search(t) and _TD_ITEMS.search(t):
        low = t.lower()
        when = next((w for w in ("overdue", "tomorrow", "week") if re.search(rf"\b{w}\b", low)), "today")
        return {"when": when, "action": "list"}
    return None


def _spoken_list(names: list[str]) -> str:
    shown = names[:5]
    if len(names) > 5:
        return ", ".join(shown) + f" and {len(names) - 5} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + f" and {shown[-1]}"


_TD_STOP = {"the", "a", "an", "my", "task", "todo", "to", "do"}


def _todoist_find(token: str, want: str) -> dict | None:
    words = [w for w in re.findall(r"[a-z0-9']+", want.lower()) if w not in _TD_STOP]
    if not words:
        return None
    hits = todoist.tasks(token, "search: " + " ".join(words))
    if not hits and len(words) > 1:
        hits = todoist.tasks(token, "search: " + max(words, key=len))
    scored = [(sum(w in str(h.get("content", "")).lower() for w in words), h) for h in hits]
    best = max(scored, key=lambda x: x[0], default=(0, None))
    return best[1] if best[0] else None


def run_todoist(a: dict, env: Env) -> Result:
    token = env.secret("todoist") if env.secret is not None else None
    if not token:
        return Result(False, TODOIST_OFF)
    action = str(a.get("action") or "list").lower()
    try:
        if action == "add":
            text = str(a.get("text") or "").strip()
            if not text:
                return Result(False, "What should I add to Todoist?")
            task = todoist.quick_add(token, text)
            content = str(task.get("content") or text)
            due = str((task.get("due") or {}).get("string") or "")
            return Result(True, f"Added {content}, due {due}." if due else f"Added {content} to Todoist.")
        if action == "complete":
            want = str(a.get("task") or "").strip()
            if not want:
                return Result(False, "Which task should I mark as done?")
            task = _todoist_find(token, want)
            if task is None:
                return Result(False, f"I couldn't find a Todoist task like {want}.")
            content = str(task.get("content") or want)
            ask = ConfirmRequest("todoist", {"task": content}, detail=f"Mark {content} as done?")
            if env.confirm is not None and not env.confirm(ask):
                return Result(False, "Okay, I'll leave it.")
            todoist.close(token, str(task["id"]))
            return Result(True, f"Marked {content} as done.")
        if action != "list":
            return Result(False, "Should I add a task, read your tasks, or mark one as done?")
        when = str(a.get("when") or "today").lower()
        when = when if when in todoist.WHEN else "today"
        names = [str(x.get("content") or "a task") for x in todoist.tasks(token, todoist.WHEN[when])]
        label = {"today": "for today", "tomorrow": "for tomorrow", "week": "for this week"}.get(when, "")
        if not names:
            return Result(True, "Nothing overdue in Todoist." if when == "overdue" else f"Nothing in Todoist {label}.")
        n = len(names)
        what = f"{n} overdue task{'s' * (n != 1)}" if when == "overdue" else f"{n} task{'s' * (n != 1)} {label}"
        return Result(True, f"You have {what}: {_spoken_list(names)}.")
    except todoist.TokenRejected:
        return Result(False, "Todoist didn't accept your token. You can change it in Tools, under Todoist.")
    except todoist.TodoistError as exc:
        return Result(False, "I couldn't reach Todoist.", str(exc))


TOOLS: tuple[Tool, ...] = (
    Tool("set_screen_brightness", "Set the display brightness to a percentage, or make the screen brighter or dimmer.",
         {"percent": {"type": "integer", "description": "0-100"},
          "action": {"type": "string", "enum": ["up", "down"]}},
         run_brightness, lambda t, e: _p_level(t), _choice("action", "Which way should the brightness go?", LEVEL_CHOICE, LEVEL_ONLY)),
    Tool("set_volume", "Turn the speaker volume up or down, mute it, or set an exact level.",
         {"percent": {"type": "integer", "description": "0-100"},
          "action": {"type": "string", "enum": ["up", "down", "mute", "unmute"]}},
         run_volume, _p_volume, _choice("action", "What should happen to the volume?", VOLUME_CHOICE, LEVEL_ONLY)),
    Tool("lock_screen", "Lock the screen.", {}, run_lock, _p_none, needs_args=False),
    Tool("take_screenshot", "Save a screenshot of the whole screen or one window to a file.",
         {"window": {"type": "string", "description": "window title, empty for the whole screen"}},
         run_screenshot, _p_none),
    Tool("open_app_or_link", "Launch an app, open a file or folder, or open a website.",
         {"target": {"type": "string", "description": "app name, path or URL"}}, run_open, _p_open,
         Choice("target", "Which installed app is the user asking to open?", _app_options), required=("target",)),
    Tool("search_web", "Search the web, YouTube, Maps, GitHub or Wikipedia in the browser.",
         {"query": {"type": "string"}, "site": {"type": "string", "enum": list(SITES)}}, run_search, _p_search,
         required=("query",)),
    Tool("control_media", "Play, pause, skip or stop whatever music or video is playing.",
         {"action": {"type": "string", "enum": list(MEDIA)}}, run_media, _p_media,
         _choice("action", "What should the media player do?", MEDIA), required=("action",)),
    Tool("toggle_night_light", "Turn the warm-screen blue-light filter (Night Light) on or off.",
         {"on": {"type": "boolean"}}, run_night_light, _p_toggle,
         _choice("on", "Should Night Light be on or off?", ON_OFF, lambda x: x == "on"), required=("on",)),
    Tool("set_do_not_disturb", "Silence or restore notification banners (Do Not Disturb).",
         {"on": {"type": "boolean"}}, run_dnd, _p_dnd,
         _choice("on", "Should Do Not Disturb be on (silence banners) or off?", ON_OFF, lambda x: x == "on"),
         required=("on",)),
    Tool("open_settings", "Open the system Settings app, optionally on a panel such as Sound, Wi-Fi or Display.",
         {"panel": {"type": "string", "enum": [""] + list(SETTINGS_PANELS)}}, run_settings, _p_settings,
         _choice("panel", "Which Settings page does the user want?", {"": "the main Settings window", **SETTINGS_PANELS})),
    Tool("open_terminal", "Open a new terminal window.", {}, run_terminal, _p_none, needs_args=False),
    Tool("open_file_manager", "Open the file manager, optionally at Downloads, Documents and so on.",
         {"folder": {"type": "string", "enum": list(FOLDERS)}}, run_files, _p_files,
         _choice("folder", "Which folder should open?", {k: k.capitalize() or "home" for k in FOLDERS})),
    Tool("clipboard", "Copy text to the clipboard or read what is on it.",
         {"action": {"type": "string", "enum": ["copy", "read"]}, "text": {"type": "string"}},
         run_clipboard, _p_clipboard, required=("action",)),
    Tool("run_command", "Run a shell command for a computer task no other tool covers.",
         {"command": {"type": "string", "description": "one POSIX shell command"}}, run_command,
         family="shell", required=("command",)),
    Tool("set_voice", "Change the assistant's own speaking voice.",
         {"voice": {"type": "string", "enum": list(VOICES)}}, run_voice, _p_voice,
         _choice("voice", "Which voice does the user want?", VOICES), family="voice", required=("voice",)),
    Tool("go_to_sleep", "Stop the assistant listening until the wake word is said again.", {}, run_sleep, _p_none,
         family="assistant", needs_args=False),
    Tool("get_time", "Tell the current time, date or day of the week.", {}, run_time, _p_none, family="info",
         needs_args=False),
    Tool("system_status", "Report this computer's battery level, network, disk space, memory or CPU load.",
         {"aspect": {"type": "string", "enum": list(STATUS)}}, run_status, _p_status,
         _choice("aspect", "What does the user want to know about?", {k: v[0] for k, v in STATUS.items()}),
         family="info"),
    Tool("describe_screen", "Look at what is on the screen right now and describe, read or explain it.",
         {"question": {"type": "string"}}, run_describe, _p_describe, family="desktop", required=("question",),
         answers=True),
    Tool("write_text", "Write a reply, message or other text from what is on screen and paste it into the text "
         "box the user is typing in.",
         {"request": {"type": "string", "description": "what to write, in the user's words"}}, run_write, _p_write,
         family="desktop", required=("request",)),
    Tool("installed_apps", "List the installed apps, or say what one installed app is for.",
         {"app": {"type": "string", "description": "an app's name, empty for the whole list"}}, run_apps, _p_apps,
         family="info", needs_args=False),
    Tool("list_folder", "Say which files are in a folder such as Downloads or Documents, without opening it.",
         {"folder": {"type": "string", "enum": list(FOLDERS)}}, run_list_folder, _p_list_folder,
         _choice("folder", "Which folder?", {k: k.capitalize() for k in FOLDERS}), family="info"),
    Tool("switch_to_app", "Bring an app that is already open to the front.",
         {"app": {"type": "string", "description": "the app's name"}}, run_switch, _p_switch,
         Choice("app", "Which app should come to the front?", _app_options), required=("app",)),
    Tool("close_app", "Close or quit an open app.",
         {"app": {"type": "string", "description": "the app's name"}}, run_close, _p_close,
         Choice("app", "Which app should close?", _app_options), required=("app",)),
    Tool("set_timer", "Set a timer or a reminder, list them, or cancel them.",
         {"action": {"type": "string", "enum": ["set", "list", "cancel"]},
          "seconds": {"type": "integer", "description": "how long from now"},
          "at": {"type": "string", "description": "a clock time, HH:MM 24-hour"},
          "message": {"type": "string", "description": "what to remind about, empty for a plain timer"},
          "which": {"type": "string", "description": "for cancel: which timer, or all"}},
         run_timer, _p_timer, family="assistant", answers=True),
    Tool("get_weather", "Tell the weather now, today or tomorrow, here or in a named city.",
         {"city": {"type": "string", "description": "empty for the user's home city"},
          "when": {"type": "string", "enum": ["now", "today", "tomorrow"]}}, run_weather, _p_weather,
         family="info", needs_args=False),
    Tool("web_lookup", "Look up a quick fact, a person, a place or a word's meaning online and say a short answer.",
         {"query": {"type": "string"}}, run_lookup, _p_lookup, family="info", required=("query",)),
    Tool("todoist", "Todoist: add a task, read today's, tomorrow's or overdue tasks, or mark a task as done.",
         {"action": {"type": "string", "enum": ["add", "list", "complete"]},
          "text": {"type": "string", "description": "for add: the task as the user said it, with any date"},
          "when": {"type": "string", "enum": ["today", "tomorrow", "overdue", "week"]},
          "task": {"type": "string", "description": "for complete: which task"}},
         run_todoist, _p_todoist, family="assistant", answers=True, required=("action",)),
)
TODOIST_TOOLS = frozenset({"todoist"})  # on the menus only while a token is saved

BY_NAME = {t.name: t for t in TOOLS}


_REMEMBER = re.compile(r"(?i)^(?:please\s+|hey\s+)?(?:can you\s+|could you\s+)?(always\s+)?remember\s+(?:that\s+)?(.+?)[.!]*$")
_FORGET = re.compile(r"(?i)^(?:please\s+)?(?:can you\s+)?forget\s+(?:that\s+|about\s+)?(.+?)[.!]*$")
_FIRST = re.compile(r"\b(i'm|i've|i'd|i'll|im|myself|mine|my|me|i)\b", re.I)  # longest first
_THIRD = {"i": "they", "i'm": "they're", "im": "they're", "i've": "they've", "i'd": "they'd", "i'll": "they'll",
          "me": "them", "my": "their", "mine": "theirs", "myself": "themselves"}


def third_person(text: str) -> str:
    """'I like tea, my sister is Priya' -> 'They like tea, their sister is Priya' (facts are about the user)."""
    out = _FIRST.sub(lambda m: _THIRD[m.group(1).lower()], text.strip())
    out = re.sub(r"\bthey am\b", "they are", out, flags=re.I)
    return out[:1].upper() + out[1:]


def parse_memory(text: str) -> tuple[str, dict] | None:
    """An explicit "remember that ..." / "forget that I ..." needs no model; anything vaguer goes to the big one."""
    t = " ".join(text.split())
    m = _REMEMBER.match(t)
    question = t.endswith("?") or (m and re.match(r"(?i)(what|when|where|who|whom|which|why|how|if|whether)\b",
                                                    m.group(2)))
    if m and not question and not re.match(r"(?i)(to|this|that|it)\b", m.group(2)) and len(m.group(2).split()) >= 2:
        return "remember", {"fact": third_person(m.group(2)), "always": bool(m.group(1))}
    m = _FORGET.match(t)
    if m and _FIRST.search(m.group(1)):
        return "forget", {"fact": third_person(m.group(1))}
    return None


# Big model only: never on the small model's routing menu (its accuracy was measured on TOOLS).
MEMORY_TOOLS: tuple[Tool, ...] = (
    Tool("remember", "Save one short fact about the user for later conversations: a preference, a person, a "
         "place, a habit or a project. Use it when they share something lasting about themselves or ask you to "
         "remember something. Not for passing details of this request.",
         {"fact": {"type": "string", "description": "the fact in a few words, about the user in the third person"},
          "always": {"type": "boolean", "description": "true only if they ask you to always remember it"}},
         run_remember, family="assistant", required=("fact",)),
    Tool("forget", "Delete a saved fact about the user when they ask you to forget it or say it is wrong.",
         {"fact": {"type": "string", "description": "the fact to forget"}}, run_forget, family="assistant",
         required=("fact",)),
)


def menu(tools: tuple[Tool, ...] = TOOLS) -> dict[str, str]:
    """The router's options: every tool plus 'none' (chat, questions, anything else)."""
    return {**{t.name: t.description for t in tools}, NONE: NONE_DESC}
