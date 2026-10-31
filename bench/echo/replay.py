"""No sound. Replays recordings through the engine's real echo.py: every recording stays on disk; nothing is installed."""
import json, sys
import numpy as np, soundfile as sf
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")); sys.path.insert(0, sys.argv[2])
from ai_linux.engine import echo
from ai_linux.engine.vad import VAD
here = sys.argv[1]; F = 512
def clip(c): return sf.read(f"{here}/{c}.wav", dtype="float32")
def db(x): return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
def run(raw, ev):
    """Frames in order; each sound is fed when its time comes and stopped when it ends, as audio.py does."""
    c = echo.load(); v = VAD(); out, P = [], []; todo = sorted([(e["t"], "feed", e) for e in ev] + [(e["end"], "stop", e) for e in ev if e.get("end")], key=lambda a: a[0])
    for i in range(0, len(raw) - F, F):
        t = i / 16000
        while todo and todo[0][0] <= t:
            _, what, e = todo.pop(0)
            if what == "feed": c.feed(*clip(e["clip"]))
            else: c.stop()
        y = c.clean(raw[i:i + F]); out.append(y); P.append(float(v(np.expand_dims(y, 0))))
    return np.concatenate(out), np.array(P)
def barges(p):
    w, first = [], None
    for i, a in enumerate(p >= .3):
        w.append(bool(a)); w = w[-12:]
        if sum(w) >= 5: return i * F / 16000
    return None
def seg(x, P, a, b): i, j = int(a * 16000), int(b * 16000); p = P[i // F:j // F]; return db(x[i:j]), 100 * (p >= .3).mean(), 100 * (p >= .5).mean(), barges(p)
for name in sys.argv[3:]:
    d = f"{here}/{name}"; m = json.load(open(f"{d}/meta.json")); ev = m["ev"]
    raw = np.fromfile(f"{d}/raw.raw", np.int16).astype(np.float32) / 32768
    refs = [e for e in ev if e["tag"] in ("echo", "dt")]
    y, P = run(raw, refs); _, R = run(raw, [])
    print(f"== {name}")
    for e in ev:
        end = e.get("end") or e["t"] + len(clip(e["clip"])[0]) / clip(e["clip"])[1]
        a, b = e["t"] + .1, end + .3; g = seg(y, P, a, b); r = seg(raw, R, a, b)
        print(f'  {e["tag"]:8}{e["clip"]:8} raw {r[0]:5.1f} dB v>.5 {r[2]:3.0f}%   cleaned {g[0]:5.1f} dB v>.3 {g[1]:3.0f}% v>.5 {g[2]:3.0f}% barge@{"never" if g[3] is None else f"{g[3]:.1f}s"}')
# talking over a reply: the near-only voice of one run mixed onto an echo-only reply of the same run, at several levels
for name in ("none2", "none3"):
    d = f"{here}/{name}"; m = json.load(open(f"{d}/meta.json")); ev = m["ev"]
    raw = np.fromfile(f"{d}/raw.raw", np.int16).astype(np.float32) / 32768
    u = next(e for e in ev if e["tag"] == "user"); e = next(x for x in ev if x["clip"] == "reply1")
    v = raw[int(u["t"] * 16000):int(u["end"] * 16000)]
    start = e["t"] + 1.0
    for gain_db in (-6, 0, 6, 12):
        mix = raw[:int((e["end"] + 1) * 16000)].copy(); s = int(start * 16000); g = 10 ** (gain_db / 20); n = min(len(v), len(mix) - s); mix[s:s + n] += g * v[:n]
        y, P = run(mix, [x for x in ev if x["clip"] in ("reply0", "reply1")])
        r = seg(y, P, start, start + n / 16000)
        print(f"  {name} user at {gain_db:+d} dB over the reply: cleaned v>.3 {r[1]:3.0f}% barge@{'never' if r[3] is None else f'{r[3]:.1f}s after they start'}")
