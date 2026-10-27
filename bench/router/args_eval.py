"""Argument filling: given the chosen tool's JSON schema, can a model pull out the values? Ollama structured outputs."""

import json
import sys
import time
import urllib.request

E = lambda *v: {"enum": list(v)}  # noqa: E731
N = lambda t: {"type": [t, "null"]}  # noqa: E731
SCHEMAS = {
    "set_screen_brightness": {"level": N("integer"), "change": {"enum": ["up", "down", None]}},
    "set_volume": {"level": N("integer"), "change": {"enum": ["up", "down", "mute", "unmute", None]}},
    "open_app_or_link": {"target": {"type": "string"}},
    "search_web": {"query": {"type": "string"}, "site": E("web", "youtube", "maps", "github", "wikipedia")},
    "control_media": {"action": E("play", "pause", "next", "previous", "stop")},
    "toggle_night_light": {"on": {"type": "boolean"}},
    "open_settings": {"panel": N("string")},
    "open_file_manager": {"location": N("string")},
    "set_timer": {"seconds": N("integer"), "at": N("string"), "label": N("string")},
    "get_weather": {"place": N("string"), "when": {"enum": ["now", "today", "tomorrow", "weekend", None]}},
    "switch_window": {"app": N("string"), "action": E("focus", "close", "minimize", "maximize")},
    "toggle_wireless": {"radio": E("wifi", "bluetooth", "airplane"), "on": {"type": "boolean"}},
    "type_text": {"text": {"type": "string"}},
}
CASES = [
    ("set_screen_brightness", "set brightness to forty percent", {"level": 40}),
    ("set_screen_brightness", "make the screen a bit brighter", {"change": "up"}),
    ("set_screen_brightness", "dim the display", {"change": "down"}),
    ("set_screen_brightness", "brightness 75", {"level": 75}),
    ("set_volume", "set the volume to thirty", {"level": 30}),
    ("set_volume", "its way too loud", {"change": "down"}),
    ("set_volume", "mute the sound", {"change": "mute"}),
    ("set_volume", "volume up a little", {"change": "up"}),
    ("set_volume", "put the volume at 15 percent", {"level": 15}),
    ("open_app_or_link", "open firefox", {"target": "firefox"}),
    ("open_app_or_link", "launch spotify please", {"target": "spotify"}),
    ("open_app_or_link", "open youtube dot com", {"target": "youtube"}),
    ("open_app_or_link", "can you start visual studio code", {"target": "code"}),
    ("search_web", "search youtube for lofi music", {"query": "lofi music", "site": "youtube"}),
    ("search_web", "google how to center a div", {"query": "center a div", "site": "web"}),
    ("search_web", "look up albert einstein on wikipedia", {"query": "albert einstein", "site": "wikipedia"}),
    ("search_web", "search maps for the nearest petrol pump", {"query": "petrol pump", "site": "maps"}),
    ("control_media", "pause the music", {"action": "pause"}),
    ("control_media", "skip this track", {"action": "next"}),
    ("control_media", "go back to the previous song", {"action": "previous"}),
    ("control_media", "resume playback", {"action": "play"}),
    ("toggle_night_light", "turn on night light", {"on": True}),
    ("toggle_night_light", "switch off the night light", {"on": False}),
    ("open_settings", "open wifi settings", {"panel": "wifi"}),
    ("open_settings", "go to display settings", {"panel": "display"}),
    ("open_settings", "open the sound settings", {"panel": "sound"}),
    ("open_file_manager", "open my downloads folder", {"location": "downloads"}),
    ("open_file_manager", "show me my documents", {"location": "documents"}),
    ("set_timer", "set a timer for ten minutes", {"seconds": 600}),
    ("set_timer", "timer five minutes for the pasta", {"seconds": 300, "label": "pasta"}),
    ("set_timer", "set an alarm for six thirty", {"at": "06:30"}),
    ("set_timer", "remind me to call mom in an hour", {"seconds": 3600, "label": "call mom"}),
    ("get_weather", "will it rain tomorrow", {"when": "tomorrow"}),
    ("get_weather", "how hot is it in pune today", {"place": "pune", "when": "today"}),
    ("get_weather", "weather forecast for the weekend in mumbai", {"place": "mumbai", "when": "weekend"}),
    ("switch_window", "switch to firefox", {"app": "firefox", "action": "focus"}),
    ("switch_window", "close chrome", {"app": "chrome", "action": "close"}),
    ("switch_window", "maximize the terminal window", {"app": "terminal", "action": "maximize"}),
    ("switch_window", "minimize spotify", {"app": "spotify", "action": "minimize"}),
    ("toggle_wireless", "turn off wifi", {"radio": "wifi", "on": False}),
    ("toggle_wireless", "switch on bluetooth", {"radio": "bluetooth", "on": True}),
    ("toggle_wireless", "enable airplane mode", {"radio": "airplane", "on": True}),
    ("type_text", "type dear john thank you for the update", {"text": "dear john thank you for the update"}),
    ("type_text", "type the word banana", {"text": "banana"}),
]


def match(gold, pred):
    for k, g in gold.items():
        p = pred.get(k)
        if isinstance(g, str) and isinstance(p, str):
            a, b = g.lower().replace(" ", ""), p.lower().replace(" ", "").replace(".com", "")
            if k == "at":
                b = b.zfill(5)
            if a not in b and b not in a:
                return False
        elif p != g:
            return False
    return True


def run(model, port):
    ok, times, bad = 0, [], []
    for tool, text, gold in CASES:
        schema = {"type": "object", "properties": SCHEMAS[tool], "required": list(SCHEMAS[tool])}
        body = {"model": model, "stream": False, "think": False, "keep_alive": -1, "format": schema,
                "options": {"temperature": 0, "num_predict": 60},
                # /api/chat ignores "format" for qwen3.5 with think off (Ollama 0.30.8); /api/generate enforces it
                "system": f"Fill in the arguments of the tool '{tool}' from the user's spoken request. Use null when "
                          "the request doesn't say. Durations in seconds, clock times as HH:MM.", "prompt": text}
        t = time.perf_counter()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        content = json.loads(urllib.request.urlopen(req, timeout=300).read())["response"]
        times.append(time.perf_counter() - t)
        try:
            pred = json.loads(content)
        except ValueError:
            pred = {}
        if match(gold, pred):
            ok += 1
        else:
            bad.append((text, gold, pred))
    times.sort()
    print(f"{model}: {ok}/{len(CASES)} correct, median {1000 * times[len(times) // 2]:.0f} ms")
    for b in bad:
        print("   miss:", b)


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]))
