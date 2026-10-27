"""Argument filling through the engine's real path: Brain.fill on the shipped tools.

Modes: "engine" = as shipped (the tool's code parser, then the small model for a choice, then the big model);
"big" = the big model alone (parsers and the small model switched off), to rate a big model by itself.
Run from the repo root: python bench/router/fill_eval.py engine qwen3.5:4b
"""

import dataclasses
import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ai_linux.engine.brain import Brain  # noqa: E402
from ai_linux.engine.tools import TOOLS, Env  # noqa: E402
from ai_linux.schema import DEFAULTS  # noqa: E402


def eq(x):
    return lambda v: v == x


def has(*words):
    return lambda v: any(w in str(v).lower() for w in words)


def starts(prefix):
    return lambda v: str(v).startswith(prefix)


CASES = [
    ("set_screen_brightness", "set brightness to forty percent", {"percent": eq(40)}),
    ("set_screen_brightness", "make the screen a bit brighter", {"action": eq("up")}),
    ("set_screen_brightness", "it's too bright, dim it", {"action": eq("down")}),
    ("set_screen_brightness", "brightness seventy five", {"percent": eq(75)}),
    ("set_screen_brightness", "put the screen at half brightness", {"percent": eq(50)}),
    ("set_screen_brightness", "set the screen to a medium level", {"percent": lambda v: 30 <= int(v) <= 70}),
    ("set_volume", "make the sound about a third of the way up", {"percent": lambda v: 25 <= int(v) <= 40}),
    ("set_volume", "set the volume to thirty", {"percent": eq(30)}),
    ("set_volume", "it's way too loud", {"action": eq("down")}),
    ("set_volume", "mute the sound", {"action": eq("mute")}),
    ("set_volume", "I can't hear anything, turn it up", {"action": eq("up")}),
    ("set_volume", "put the volume at 15 percent", {"percent": eq(15)}),
    ("set_volume", "unmute please", {"action": eq("unmute")}),
    ("open_app_or_link", "open firefox", {"target": has("firefox")}),
    ("open_app_or_link", "can you start visual studio code", {"target": has("code")}),
    ("open_app_or_link", "open youtube dot com", {"target": has("youtube")}),
    ("open_app_or_link", "launch the files app", {"target": has("files", "nautilus")}),
    ("search_web", "search youtube for lofi music", {"query": has("lofi"), "site": eq("youtube")}),
    ("search_web", "google how to center a div", {"query": has("center a div")}),
    ("search_web", "look up albert einstein on wikipedia", {"query": has("einstein"), "site": eq("wikipedia")}),
    ("search_web", "find the nearest petrol pump on maps", {"query": has("petrol"), "site": eq("maps")}),
    ("search_web", "search github for whisper cpp", {"query": has("whisper"), "site": eq("github")}),
    ("control_media", "pause the music", {"action": eq("pause")}),
    ("control_media", "skip this track", {"action": eq("next")}),
    ("control_media", "go back to the previous song", {"action": eq("previous")}),
    ("control_media", "resume playback", {"action": eq("play")}),
    ("control_media", "stop the video", {"action": eq("stop")}),
    ("toggle_night_light", "turn on night light", {"on": eq(True)}),
    ("toggle_night_light", "switch off the warm screen colours", {"on": eq(False)}),
    ("set_do_not_disturb", "don't let notifications bother me for a while", {"on": eq(True)}),
    ("set_do_not_disturb", "show my notifications again", {"on": eq(False)}),
    ("open_settings", "open wifi settings", {"panel": eq("wifi")}),
    ("open_settings", "go to display settings", {"panel": has("display")}),
    ("open_settings", "I want to change my bluetooth settings", {"panel": eq("bluetooth")}),
    ("open_settings", "the settings page where my speakers are", {"panel": eq("sound")}),
    ("open_settings", "open the battery settings", {"panel": eq("power")}),
    ("open_file_manager", "open my downloads folder", {"folder": eq("downloads")}),
    ("open_file_manager", "show me my documents", {"folder": eq("documents")}),
    ("open_file_manager", "where are my photos", {"folder": eq("pictures")}),
    ("clipboard", "copy hello world to the clipboard", {"action": eq("copy"), "text": has("hello world")}),
    ("clipboard", "what's on my clipboard", {"action": eq("read")}),
    ("set_voice", "use a female voice", {"voice": starts("supertonic:F")}),
    ("set_voice", "switch to a male voice", {"voice": starts("supertonic:M")}),
    ("system_status", "how much battery is left", {"aspect": eq("battery")}),
    ("system_status", "am I running out of disk space", {"aspect": eq("disk")}),
    ("system_status", "how much ram is free", {"aspect": eq("memory")}),
    ("system_status", "is the processor busy", {"aspect": eq("cpu")}),
    ("system_status", "am I connected to the internet", {"aspect": eq("network")}),
    ("run_command", "show the kernel version", {"command": has("uname")}),
    ("run_command", "how long has this computer been on", {"command": has("uptime")}),
    ("run_command", "list the files in my home folder", {"command": has("ls")}),
    ("describe_screen", "what does this error on my screen mean", {"question": has("error")}),
    ("write_text", "reply that I'll be there at five", {"request": has("five", "5")}),
]


def main(mode: str, model: str) -> None:
    settings = {**DEFAULTS, "big_model": model, "small_model": "jiwo-0.8b" if mode == "engine" else "none",
                "ollama_keep_alive": "1m"}
    brain = Brain(settings.get, Env(None), speak=lambda t: None)
    by_name = {t.name: t for t in TOOLS}
    if mode == "big":
        by_name = {n: dataclasses.replace(t, parse=None, choice=None, needs_args=True) for n, t in by_name.items()}
    brain.warm()
    rows, cancel = [], threading.Event()
    for name, text, want in CASES:
        t0 = time.monotonic()
        args, who = brain.fill(by_name[name], text, cancel)
        dt = time.monotonic() - t0
        ok = args is not None and all(k in args and check(args[k]) for k, check in want.items())
        rows.append({"tool": name, "text": text, "args": args, "who": who, "ok": ok, "s": round(dt, 3)})
    right = sum(r["ok"] for r in rows)
    print(f"{mode} {model}: {right}/{len(rows)} correct")
    for who in sorted({r["who"] for r in rows}):
        part = [r for r in rows if r["who"] == who]
        print(f"  filled by {who}: {sum(r['ok'] for r in part)}/{len(part)} correct, "
              f"median {statistics.median(r['s'] for r in part) * 1000:.0f} ms")
    for r in rows:
        if not r["ok"]:
            print(f"  miss: {r['tool']} {r['text']!r} -> {r['args']} ({r['who']})")
    out = Path(__file__).resolve().parents[1] / "results" / f"fill_{mode}_{model.replace(':', '_')}.json"
    out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
