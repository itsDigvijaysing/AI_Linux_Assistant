"""Cancels our own voice inside the engine: WebRTC's AEC3 (pywebrtc-audio), given the exact sound we are playing.

It runs only while we make a sound; the rest of the time the mic passes untouched, so it can't deafen Phoenix. A late
frame loses nothing, since pw-record keeps the audio: unlike PipeWire's module there is no real-time deadline to miss
(the measurements are in bench/README.md).
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
BLOCK = 160  # AEC3's 10 ms; pywebrtc-audio zero-pads anything shorter, which would tear the signal
TAIL_S = 0.5  # the speaker and the room still carry the sound this long after it stops


def load() -> Canceller | None:
    """None when the library is missing: Phoenix then doesn't listen while it speaks."""
    try:
        from pywebrtc_audio import EchoCanceller

        return Canceller(EchoCanceller(sample_rate=RATE))
    except Exception as exc:  # noqa: BLE001 - a wheel that won't load is the same as a missing one
        log.warning("echo cancelling isn't available: %s", exc)
        return None


def _to_16k(sound: np.ndarray, rate: int) -> np.ndarray:
    x = np.asarray(sound, np.float32).reshape(-1)
    if rate == RATE or not x.size:
        return x
    if rate > RATE:  # a rough low-pass, or the voice's highs fold back into the band the mic hears
        width = round(rate / RATE)
        x = np.convolve(x, np.ones(width) / width, "same")
    n = int(x.size * RATE / rate)
    return np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)


class Canceller:
    def __init__(self, aec) -> None:
        self._aec = aec
        self._lock = threading.Lock()
        self.ok = True
        self._forget()

    def _forget(self) -> None:
        self._sounds: dict[object, np.ndarray] = {}  # what is still to play of each sound, from now on
        self._near = self._out = np.zeros(0, np.float32)
        self._tail = 0

    def feed(self, sound: np.ndarray, rate: int, key: object = None) -> object:
        """A sound starts playing now; over one still playing, the room hears both. The key names it for stop."""
        x = _to_16k(sound, rate)
        key = object() if key is None else key
        with self._lock:
            if x.size:
                self._sounds[key] = x
            self._tail = int(TAIL_S * RATE)
        return key

    def stop(self, key: object = None) -> None:
        """A sound ended or was cut: what wasn't played is not in the room, what was still is for a moment.

        With a key only that sound goes, so a cue ending doesn't take the sentence under it; without one, all go."""
        with self._lock:
            if key is None:
                self._sounds.clear()
            else:
                self._sounds.pop(key, None)
            self._tail = int(TAIL_S * RATE)

    def reset(self) -> None:
        """The mic closed: nothing paces the sound any more."""
        with self._lock:
            self._forget()

    def pending(self) -> int:
        return max((s.size for s in self._sounds.values()), default=0)

    def clean(self, frame: np.ndarray) -> np.ndarray:
        """One mic frame, with our own sound taken out; the same length, 10 ms later while it runs."""
        with self._lock:
            if not self.ok or (not self._sounds and self._tail <= 0):
                self._near = self._out = np.zeros(0, np.float32)
                return frame
            try:
                return self._run(frame)
            except Exception:
                log.exception("echo cancelling failed; back to not listening while speaking")
                self.ok = False
                return frame

    def _run(self, frame: np.ndarray) -> np.ndarray:
        near = np.concatenate((self._near, frame))
        whole = near.size // BLOCK * BLOCK
        out = [self._out if self._out.size else np.zeros(BLOCK, np.float32)]  # the frame stays whole on the first go
        for i in range(0, whole, BLOCK):
            far, played = np.zeros(BLOCK, np.float32), 0
            for key, sound in list(self._sounds.items()):  # overlapping sounds mix in the room
                far[:min(sound.size, BLOCK)] += sound[:BLOCK]
                played = max(played, min(sound.size, BLOCK))
                if sound.size > BLOCK:
                    self._sounds[key] = sound[BLOCK:]
                else:
                    del self._sounds[key]
            self._tail -= BLOCK - played
            out.append(self._aec.process(near[i:i + BLOCK], far))
        self._near = near[whole:]
        done = np.concatenate(out)
        self._out = done[frame.size:]
        return done[:frame.size]
