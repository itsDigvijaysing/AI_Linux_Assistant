"""Vision check: questions about a known screenshot, answers scored by keywords. Usage: vision_check.py image port model..."""

import base64
import json
import sys
import time
import urllib.request

QUESTIONS = [
    ("Which app is this and which page is open?", [["ai linux"], ["models"]]),
    ("What Ollama version is shown?", [["0.40.11"]]),
    ("Which model is loaded right now?", [["gemma4", "gemma 4"]]),
    ("Which small model is selected, and is it installed?", [["0.8b", "jev"], ["installed"]]),
    ("What does the button next to Laya say?", [["download"]]),
]


def ask(port, model, img, q):
    body = {"model": model, "stream": False, "think": False, "keep_alive": "2m", "options": {"temperature": 0, "num_predict": 120},
            "messages": [{"role": "user", "content": q + " Answer in one short sentence.", "images": [img]}]}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    r = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return r["message"]["content"].strip(), time.perf_counter() - t


img = base64.b64encode(open(sys.argv[1], "rb").read()).decode()
for model in sys.argv[3:]:
    ask(sys.argv[2], model, img, "Describe this image.")  # load + warm
    ok, times = 0, []
    for q, need in QUESTIONS:
        a, s = ask(sys.argv[2], model, img, q)
        times.append(s)
        hit = all(any(k in a.lower() for k in alts) for alts in need)
        ok += hit
        print(f"  {model:12s} {'OK ' if hit else 'MISS'} {s:4.1f}s  {q}  ->  {a[:110]}", flush=True)
    print(f"{model}: {ok}/{len(QUESTIONS)} correct, median {sorted(times)[len(times) // 2]:.1f} s", flush=True)
    req = urllib.request.Request(f"http://127.0.0.1:{sys.argv[2]}/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode())
    urllib.request.urlopen(req)
