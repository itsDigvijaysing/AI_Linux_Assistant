"""Words while you speak: the utterance so far is decoded again in the background, one decode at a time.

Pacing is OpenWhispr's dictation preview (never two decodes at once, skip quiet audio); which words have settled
is whisper-flow's common prefix (MIT, LICENSE.whisper-flow). Commands stop at 15 s, so the whole utterance is
decoded each time instead of whisper-flow's committed window.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable

import numpy as np

EVERY_S = 0.6  # between updates; slower when decoding is slow
MIN_S = 0.8  # shorter audio decodes to noise ("Mm-hmm.")


def normalize(word: str) -> str:
    """Lowercase without punctuation, so "five." and "Five" count as the same word."""
    return re.sub(r"[^\w']", "", word.lower())


def settled(prev: list[str], words: list[str]) -> int:
    """How many leading words two updates agree on."""
    count = 0
    for old, new in zip(prev, words):
        if normalize(old) != normalize(new):
            break
        count += 1
    return count


class LiveText:
    """Feed it the growing utterance with ``offer``; it calls ``on_text(utt, text)`` from its own thread."""

    def __init__(self, transcribe: Callable[[np.ndarray], str], lock: threading.Lock,
                 on_text: Callable[[int, str], None], every: float = EVERY_S, min_s: float = MIN_S,
                 rate: int = 16000):
        self._transcribe, self._lock, self._on_text = transcribe, lock, on_text
        self.every, self.min_samples = every, int(min_s * rate)
        self._utt = -1
        self._next_at = 0.0
        self._job: tuple[int, np.ndarray] | None = None
        self._busy = False
        self._cv = threading.Condition()
        threading.Thread(target=self._run, name="LiveText", daemon=True).start()

    def begin(self, utt: int) -> None:
        with self._cv:
            self._drop()
            self._utt, self._next_at = utt, 0.0

    def end(self) -> None:
        """Drop the update in flight: the final decode takes over."""
        with self._cv:
            self._drop()
            self._utt = -1

    def _drop(self) -> None:
        if self._job is not None:  # not picked up yet; a decode already running clears busy itself
            self._job, self._busy = None, False

    def due(self, samples: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self._utt >= 0 and not self._busy and samples >= self.min_samples and now >= self._next_at

    def offer(self, utt: int, audio: np.ndarray) -> bool:
        with self._cv:
            if utt != self._utt or self._busy:
                return False
            self._job, self._busy = (utt, audio), True
            self._cv.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._job is None:
                    self._cv.wait()
                utt, audio = self._job
                self._job = None
            t0 = time.monotonic()
            try:
                with self._lock:
                    text = (self._transcribe(audio) or "").strip() if utt == self._utt else None
            except Exception:
                text = None
            took = time.monotonic() - t0
            with self._cv:
                self._busy = False
                self._next_at = time.monotonic() + max(self.every, 2 * took)  # a slow device updates less often
                current = utt == self._utt
            if text is not None and current:
                self._on_text(utt, text)
