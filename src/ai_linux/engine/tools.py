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
from ai_linux.paths import REPO_ROOT

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


@dataclass
class Env:
    """What tools may use. Built by the engine; tests pass fakes."""

    executor: Any
    confirm: Callable | None = None
    set_voice: Callable[[str], bool] | None = None
    sleep: Callable[[], None] | None = None
    vision: Callable[[str], str] | None = None
    compose: Callable[[str], str] | None = None
    cue: Callable[[str], None] | None = None  # plays "ok", "fail" or "big"
    apps: Callable[[], list] = appdb.installed


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


def run_brightness(a: dict, env: Env) -> Result:
    if "percent" in a and a["percent"] is not None:
        p = _clamp(a["percent"], 1)
        return _argv(env, "set_screen_brightness", ["brightnessctl", "-q", "set", f"{p}%"],
                     f"Brightness {p} percent.", "I couldn't change the brightness.", a)
    step = _clamp(a.get("step") or 20, 1)
    change = {"up": f"+{step}%", "down": f"{step}%-"}.get(str(a.get("action", "")).lower())
    if not change:
        return Result(False, "How bright should the screen be?")
    return _argv(env, "set_screen_brightness", ["brightnessctl", "-q", "set", change],
                 "Brighter." if change.startswith("+") else "Dimmer.", "I couldn't change the brightness.", a)


def run_volume(a: dict, env: Env) -> Result:
    if a.get("percent") is not None and int(a["percent"]) >= 0:
        p = _clamp(a["percent"])
        return _argv(env, "set_volume", ["wpctl", "set-volume", SINK, f"{p}%"], f"Volume {p} percent.",
                     "I couldn't change the volume.", a)
    act = str(a.get("action", "")).lower()
    step = _clamp(a.get("step") or 10, 1)
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


VOICES = {"supertonic:M1": "male voice 1 (default)", "supertonic:M3": "male voice 3", "supertonic:M4": "male voice 4",
          "supertonic:M5": "male voice 5", "supertonic:F1": "female voice 1", "supertonic:F2": "female voice 2",
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
    return Result(True, "Okay.")


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


CTRL_V = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]  # evdev codes: left Ctrl + V


def paste_text(env: Env, text: str, args: dict) -> Result:
    """Puts text where the user's cursor is: clipboard + Ctrl+V, then gives the old clipboard back.
    Never presses Enter, so nothing is sent."""
    def run(argv: list[str]) -> dict:
        return env.executor.run_argv(argv, tool_name="desktop.write_text", args=args, confirm=env.confirm,
                                     resource_caps=False)  # wl-copy keeps serving the clipboard after it returns
    old = run(["wl-paste", "--no-newline", "--type", "text/plain"])
    put = _result(run(["wl-copy", "--", text]), "", "I couldn't use the clipboard.")
    if not put.ok:
        return Result(False, put.say, put.output)
    time.sleep(0.15)
    if "error" in (keys := run(CTRL_V)) or keys.get("returncode", 0) != 0:
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
)

BY_NAME = {t.name: t for t in TOOLS}


def menu(tools: tuple[Tool, ...] = TOOLS) -> dict[str, str]:
    """The router's options: every tool plus 'none' (chat, questions, anything else)."""
    return {**{t.name: t.description for t in tools}, NONE: NONE_DESC}
