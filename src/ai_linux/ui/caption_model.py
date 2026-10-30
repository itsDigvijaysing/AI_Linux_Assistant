"""The pill's caption without GTK: what it shows, where the words sit and how they move (tested headless).

While you speak it is one fixed-width line: words stay centred while they fit, then new ones enter at the right
and the line slides left, fading out at the edge. While the assistant speaks it grows into a rounded card with
the sentence being said, then shrinks back into the capsule.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

LIVE_W = 320  # the live line's fixed width, about twice the capsule
REPLY_W = 420  # widest the reply card grows
PAD_X, PAD_Y = 16, 9
MAX_LINES = 4  # per card; a longer reply turns pages as it is spoken
CHARS_PER_S = 14.0  # speaking pace, when the engine sends no length
RADIUS = 18
FADE = 28  # px over which words fade at the line's edges
DIM = 0.45  # words that may still change
TAU_MOVE = 0.11  # s for the words and the card's size to catch up (about 95 % in 0.33 s)
TAU_FADE_IN, TAU_FADE_OUT = 0.07, 0.14  # fading out slower than it shrinks shows the collapse

# Frosted glass, mostly opaque: GNOME gives an app no pixels behind its window to blur (pill-safety.css matches).
GLASS = {
    False: {"fill": (0.988, 0.988, 0.992, 0.86), "sheen": (1, 1, 1, 0.55), "border": (0, 0, 0.024, 0.09),
            "edge": (1, 1, 1, 0.9), "shadow": (0, 0, 0.024, 0.20), "text": (0.11, 0.11, 0.13, 1.0)},
    True: {"fill": (0.141, 0.141, 0.157, 0.84), "sheen": (1, 1, 1, 0.07), "border": (1, 1, 1, 0.12),
           "edge": (1, 1, 1, 0.08), "shadow": (0, 0, 0, 0.50), "text": (0.957, 0.957, 0.965, 1.0)},
}


def live_markup(text: str, stable: int, dim: float = DIM) -> str:
    """Pango markup: the settled words solid, the rest dimmed."""
    words = text.split()
    stable = max(0, min(stable, len(words)))
    solid = escape(" ".join(words[:stable]))
    rest = escape(" ".join(words[stable:]))
    if not rest:
        return solid
    sep = " " if solid else ""
    return f'{solid}{sep}<span alpha="{round(dim * 100)}%">{rest}</span>'


def scroll_target(text_w: float, box_w: float) -> float:
    """Where the line starts inside its box: centred while it fits, else its end at the right edge."""
    return (box_w - text_w) / 2 if text_w <= box_w else box_w - text_w


def edge_fades(x: float, text_w: float, box_w: float, fade: float = FADE) -> tuple[float, float]:
    """How strongly each edge fades (0..1): only where words run past it."""
    left = min(1.0, max(0.0, -x / fade))
    right = min(1.0, max(0.0, (x + text_w - box_w) / fade))
    return left, right


def reply_width(natural_w: float) -> float:
    """The card is as wide as its sentence needs, between the live line and REPLY_W."""
    return float(min(REPLY_W, max(LIVE_W, math.ceil(natural_w) + 2 * PAD_X)))


def balanced_width(lines_at, widest: int, narrowest: int, step: int = 4) -> int:
    """The narrowest width that keeps the line count ``widest`` gives, so lines come out even (no lone word)."""
    lines = lines_at(widest)
    if lines <= 1:
        return widest
    lo, hi = max(1, min(narrowest, widest)), widest
    if lines_at(lo) <= lines:
        return lo
    while hi - lo > step:
        mid = (lo + hi) // 2
        if lines_at(mid) > lines:
            lo = mid
        else:
            hi = mid
    return hi


def _greedy(words: list[str], fits) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for w in words:
        if cur and not fits(" ".join(cur + [w])):
            cut = next((i + 1 for i in range(len(cur) - 1, max(0, len(cur) - 9), -1) if cur[i][-1] in ",.;:!?"),
                       len(cur))
            out.append(" ".join(cur[:cut]))
            cur = cur[cut:]
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out or [""]


def pages(text: str, fits) -> list[str]:
    """A reply too long for one card, split into cards that each fit (``fits(text) -> bool``) and are about
    the same length (no lone last words), cut after a comma or full stop when one is near."""
    words = text.split()
    plain = _greedy(words, fits)
    if len(plain) < 2:
        return plain
    most = len(text) / len(plain) * 1.15
    even = _greedy(words, lambda s: len(s) <= most and fits(s))
    return even if len(even) == len(plain) else plain


def page_starts(cards: list[str], seconds: float) -> list[float]:
    """When each card comes up, in seconds from the start of speech: in proportion to its length."""
    total = sum(len(c) for c in cards) or 1
    seconds = seconds if seconds > 0 else total / CHARS_PER_S
    starts, done = [], 0
    for c in cards:
        starts.append(seconds * done / total)
        done += len(c)
    return starts


def approach(cur: float, target: float, dt: float, tau: float, snap: float = 0.3) -> float:
    """Ease ``cur`` toward ``target`` (frame-rate independent); lands exactly once within ``snap``."""
    if tau <= 0 or abs(target - cur) <= snap:
        return target
    return target + (cur - target) * math.exp(-max(0.0, dt) / tau)


class CaptionModel:
    """Which caption is up: hidden, live (your words) or reply (what the assistant is saying)."""

    def __init__(self):
        self.mode = "hidden"
        self.text = ""
        self.stable = 0

    def live(self, text: str, stable: int, final: bool) -> bool:
        text = " ".join(text.split())
        if not text:
            return self.mode == "live" and self.hide()  # an empty update clears your words, never a reply
        n = len(text.split())
        new = ("live", text, n if final else max(0, min(stable, n)))
        if new == (self.mode, self.text, self.stable):
            return False
        self.mode, self.text, self.stable = new
        return True

    def saying(self, text: str) -> bool:
        text = " ".join(text.split())
        if not text or (self.mode, self.text) == ("reply", text):
            return False
        self.mode, self.text, self.stable = "reply", text, 0
        return True

    def state(self, state: str) -> bool:
        """Thinking and speaking keep what is up; listening drops an old reply; anything else hides it."""
        if state in ("thinking", "speaking"):
            return False
        if state == "listening" and self.mode != "reply":
            return False
        return self.hide()

    def hide(self) -> bool:
        if self.mode == "hidden":
            return False
        self.mode, self.text, self.stable = "hidden", "", 0
        return True
