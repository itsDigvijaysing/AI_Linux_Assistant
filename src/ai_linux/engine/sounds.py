"""Short synthesised cues, played with pw-play so they need no sound files.

listen: the assistant is listening. ok / fail: an action finished or failed (instead of speaking "Done").
big: the request went to the big model, so the answer takes a moment longer.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import wave

import numpy as np

RATE = 24000
# (frequency Hz, start s, length s) per note
NOTES = {
    "listen": ((880, 0.0, 0.09),),
    "ok": ((1047, 0.0, 0.07), (1319, 0.07, 0.10)),
    "fail": ((659, 0.0, 0.11), (440, 0.12, 0.16)),
    "big": ((784, 0.0, 0.12), (988, 0.06, 0.12), (1175, 0.12, 0.18)),
}


def tone(kind: str, volume: float = 1.0) -> np.ndarray:
    notes = NOTES[kind]
    out = np.zeros(int(RATE * max(s + n for _f, s, n in notes)) + 1, dtype=np.float32)
    for freq, start, length in notes:
        t = np.arange(int(RATE * length)) / RATE
        i = int(RATE * start)
        out[i:i + len(t)] += np.sin(2 * np.pi * freq * t) * np.hanning(len(t))
    return (out / max(1.0, float(np.abs(out).max())) * 0.18 * max(0.0, min(volume, 1.5))).astype(np.float32)


def _play(kind: str, sink: str | None, volume: float) -> None:
    fd, path = tempfile.mkstemp(suffix=".wav", dir=os.environ.get("XDG_RUNTIME_DIR"))
    try:
        with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((tone(kind, volume) * 32767).astype(np.int16).tobytes())
        subprocess.run(["pw-play", *(["--target", sink] if sink else []), path], timeout=3,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    finally:
        os.unlink(path)


def play(kind: str, sink: str | None = None, volume: float = 1.0) -> None:
    """Plays in the background; unknown kinds and a missing pw-play are ignored."""
    if kind in NOTES:
        threading.Thread(target=_play, args=(kind, sink, volume), name="Cue", daemon=True).start()
