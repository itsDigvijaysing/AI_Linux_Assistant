"""Phoenix's sounds: short synthesised bell tones in D major pentatonic, played with pw-play (no sound files).

Pure Python (no numpy), so the app can preview them on the Voice page too. Rising means awake or done,
falling means going quiet or failed; "big" is a warm rising swell with a breath of flame.
"""

from __future__ import annotations

import array
import functools
import math
import os
import random
import subprocess
import tempfile
import threading
import wave

RATE = 24000
GAIN = 0.2
_PARTIALS = ((1.0, 1.0), (2.0, 0.32), (3.0, 0.11), (4.2, 0.04))  # bell-like overtones

D5, E5, FS5, A5, B5, D6, FS6, A6 = 587.3, 659.3, 740.0, 880.0, 987.8, 1174.7, 1480.0, 1760.0
# (frequency Hz, start s, ring s, loudness, glide to Hz or 0, swell: fade in instead of a struck bell)
NOTES = {
    "listen": ((A5, 0.0, 0.16, 0.8, 0, False), (D6, 0.07, 0.30, 1.0, 0, False)),
    "ok": ((D6, 0.0, 0.18, 0.7, 0, False), (FS6, 0.07, 0.2, 0.75, 0, False), (A6, 0.14, 0.5, 0.9, 0, False)),
    "fail": ((FS5, 0.0, 0.22, 0.9, 0, False), (D5, 0.13, 0.45, 0.8, 0, False)),
    "unclear": ((E5, 0.0, 0.18, 0.7, 0, False), (A5, 0.12, 0.36, 0.75, B5, False)),
    "big": ((D5, 0.0, 0.62, 0.5, D6, True), (A5, 0.45, 0.45, 0.6, 0, False), (D6, 0.55, 0.6, 0.75, 0, False)),
    "sleep": ((D6, 0.0, 0.2, 0.7, 0, False), (A5, 0.1, 0.25, 0.6, 0, False), (D5, 0.22, 0.6, 0.5, 0, False)),
    "timer": tuple(n for k in range(3) for n in ((D6, 0.5 * k, 0.16, 0.8, 0, False), (A6, 0.5 * k + 0.12, 0.3, 0.9,
                                                                                     0, False))),
}
WHOOSH = {"big": (0.0, 0.7)}  # (start, length) of soft filtered noise: the flame
LABELS = {
    "listen": "Listening", "ok": "Done", "fail": "Failed", "unclear": "Didn't catch that",
    "big": "Big model waking", "sleep": "Back to sleep", "timer": "Timer or reminder",
}


def _note(out: list[float], freq: float, start: float, ring: float, loud: float, glide: float, swell: bool) -> None:
    i0, n = int(RATE * start), int(RATE * ring)
    phase = [0.0] * len(_PARTIALS)
    for k in range(n):
        t = k / RATE
        f = freq if not glide else freq * (glide / freq) ** min(1.0, t / (ring * 0.8))
        env = (math.sin(math.pi * k / n) ** 1.5 if swell
               else min(1.0, t / 0.004) * math.exp(-t / (ring * 0.35)) * (1 - k / n) ** 0.5)
        s = 0.0
        for j, (mult, amp) in enumerate(_PARTIALS):
            phase[j] += 2 * math.pi * f * mult / RATE
            s += amp * math.sin(phase[j])
        out[i0 + k] += loud * env * s


def _whoosh(out: list[float], start: float, length: float) -> None:
    rnd, low, i0, n = random.Random(7), 0.0, int(RATE * start), int(RATE * length)
    for k in range(n):
        x = k / n
        a = 0.02 + 0.10 * x  # the filter opens as it rises
        low += a * (rnd.uniform(-1, 1) - low)
        out[i0 + k] += 0.9 * low * math.sin(math.pi * x) ** 2


@functools.lru_cache(maxsize=16)
def _shape(kind: str) -> tuple[float, ...]:
    """The sound at full scale, made once (10-40 ms in pure Python)."""
    notes = NOTES[kind]
    end = max(n[1] + n[2] for n in notes)
    if kind in WHOOSH:
        end = max(end, sum(WHOOSH[kind]))
    out = [0.0] * (int(RATE * end) + 1)
    for note in notes:
        _note(out, *note)
    if kind in WHOOSH:
        _whoosh(out, *WHOOSH[kind])
    peak = max(1e-9, max(abs(x) for x in out))
    return tuple(x / peak for x in out)


def tone(kind: str, volume: float = 1.0) -> list[float]:
    g = GAIN * max(0.0, min(volume, 1.5))
    return [x * g for x in _shape(kind)]


def write_wav(kind: str, path: str, volume: float = 1.0) -> None:
    pcm = array.array("h", (int(max(-1.0, min(1.0, x)) * 32767) for x in tone(kind, volume)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def _play(kind: str, volume: float, on_play) -> None:
    fd, path = tempfile.mkstemp(suffix=".wav", dir=os.environ.get("XDG_RUNTIME_DIR"))
    os.close(fd)
    try:
        write_wav(kind, path, volume)
        if on_play is not None:
            on_play(tone(kind, volume), RATE)
        subprocess.run(["pw-play", path], timeout=4,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    finally:
        os.unlink(path)


def play(kind: str, volume: float = 1.0, on_play=None) -> None:
    """Plays in the background; unknown kinds and a missing pw-play are ignored. ``on_play(samples, rate)``: it starts
    now."""
    if kind in NOTES:
        threading.Thread(target=_play, args=(kind, volume, on_play), name="Cue", daemon=True).start()
