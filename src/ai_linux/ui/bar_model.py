"""Pure animation model for the 11-bar waveform indicator (no GTK, unit-testable).

Shared by the in-window widget, the floating pill and the tray icon so all three move alike.
Final bar height in pixels = geometry.min_h + (geometry.max_h - geometry.min_h) * frame.heights[i].
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

N_BARS = 11
CENTER = N_BARS // 2
# Fixed taper from the user's reference icon: tallest in the centre.
TAPER = (0.30, 0.42, 0.56, 0.72, 0.88, 1.00, 0.88, 0.72, 0.56, 0.42, 0.30)

COLORS = {
    "off": "#c0bfbc",
    "idle": "#62a0ea",
    "followup": "#3584e4",
    "listening": "#3584e4",
    "thinking": "#3584e4",
    "loading": "#9a9996",
    "speaking": "#1a5fb4",
    "muted": "#77767b",
    "error": "#e01b24",
    "resting": "#99c1f1",
}

# Frame rates per state; 0 means draw once and stop ticking.
FPS = {
    "off": 0, "idle": 20, "followup": 20, "listening": 60, "thinking": 30,
    "loading": 20, "speaking": 60, "muted": 0, "error": 0,
}

BLEND_S = 0.18
MUTE_BLEND_S = 0.22
SHAKE_S = 0.40
QUIET = 0.002  # level below which a listening bar is drawn at rest (under 0.2 px on the hero)


@dataclass(frozen=True)
class Geometry:
    bar_w: float
    gap: float
    min_h: float
    max_h: float

    @property
    def width(self) -> float:
        return N_BARS * self.bar_w + (N_BARS - 1) * self.gap


GEOMETRY = {
    "hero": Geometry(10, 8, 10, 96),
    "pill": Geometry(4, 3, 4, 24),
    "resting": Geometry(2, 2, 2, 8),
    "tray": Geometry(2, 1, 2, 22),  # 32 px wide icon
}


@dataclass
class Frame:
    heights: list[float]  # 0..1 per bar, taper already applied
    color: str
    opacities: list[float] = field(default_factory=lambda: [1.0] * N_BARS)
    x_offset: float = 0.0  # pixels, error shake
    progress: float | None = None  # follow-up window time left, 0..1


def ease_out_cubic(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def level_from_rms(rms: float, floor_db: float = -52.0, ceil_db: float = -14.0) -> float:
    """Map float PCM RMS (0..1) to a perceptual 0..1 bar level."""
    db = 20 * math.log10(max(rms, 1e-9))
    x = (db - floor_db) / (ceil_db - floor_db)
    return min(1.0, max(0.0, x)) ** 0.7


class LevelFollower:
    """Frame-rate independent attack/release smoothing."""

    def __init__(self, attack_s: float, release_s: float):
        self.attack_s, self.release_s = attack_s, release_s
        self.value = 0.0

    def update(self, target: float, dt: float) -> float:
        tau = self.attack_s if target > self.value else self.release_s
        alpha = 1 - math.exp(-max(dt, 0.0) / tau)
        self.value += (target - self.value) * alpha
        return self.value


class BarAnimator:
    """Turns (state, time, audio level) into per-bar heights. Call ``step`` once per frame."""

    def __init__(self, reduced_motion: bool = False):
        self.reduced_motion = reduced_motion
        self.state = "idle"
        self._state_t0 = 0.0
        self._blend_from: list[float] | None = None
        self._blend_s = BLEND_S
        self._last: list[float] = [0.30 * e for e in TAPER]
        self._last_t: float | None = None
        self._mic = LevelFollower(0.040, 0.160)
        self._tts = LevelFollower(0.030, 0.120)
        self._ring: deque[tuple[float, float]] = deque(maxlen=64)  # (time, smoothed mic level)

    def set_state(self, state: str, now: float) -> None:
        state = str(getattr(state, "value", state))  # accept AssistantState members too
        if state == self.state:
            return
        self._blend_from = list(self._last)
        self._blend_s = MUTE_BLEND_S if state == "muted" else BLEND_S
        self.state, self._state_t0 = state, now

    def fps(self) -> int:
        if self.reduced_motion and self.state in ("idle", "followup", "thinking", "loading"):
            return 0
        return FPS.get(self.state, 0)

    def is_settled(self, now: float, level: float | None = None) -> bool:
        """True when nothing will change until the state or level does.

        Pass the current ``level`` to also let a silent listening state settle (its bars are static then).
        """
        blending = self._blend_from is not None and now - self._state_t0 < self._blend_s
        shaking = self.state == "error" and not self.reduced_motion and now - self._state_t0 < SHAKE_S
        if blending or shaking:
            return False
        if self.fps() == 0:
            return True
        return level is not None and self.state == "listening" and self._mic_quiet(now, level)

    def _mic_quiet(self, now: float, level: float) -> bool:
        # The bars read the ring up to 0.035 * CENTER s back, so it must be quiet over that span too.
        if level >= QUIET or self._mic.value >= QUIET:
            return False
        horizon = now - 0.035 * CENTER - 0.05
        return all(v < QUIET for ts, v in self._ring if ts >= horizon)

    def step(self, now: float, level: float = 0.0, progress: float | None = None) -> Frame:
        dt = 0.0 if self._last_t is None else now - self._last_t
        self._last_t = now
        st = self.state
        t = now - self._state_t0
        amps, opac, color, xoff = self._target(st, now, t, level, dt)
        heights = [min(1.0, max(0.0, a)) * e for a, e in zip(amps, TAPER)]
        if self._blend_from is not None:
            u = (now - self._state_t0) / self._blend_s
            if u >= 1:
                self._blend_from = None
            else:
                k = ease_out_cubic(u)
                heights = [f + (h - f) * k for f, h in zip(self._blend_from, heights)]
        self._last = heights
        return Frame(heights, color, opac, xoff, progress if st == "followup" else None)

    def _target(self, st, now, t, level, dt):
        ones = [1.0] * N_BARS
        if st in ("idle", "followup"):
            if self.reduced_motion:
                return [0.34] * N_BARS, ones, COLORS[st], 0.0
            amps = [
                0.30 + 0.08 * (0.5 - 0.5 * math.cos(2 * math.pi * (now - 0.12 * abs(i - CENTER)) / 3.6))
                for i in range(N_BARS)
            ]
            return amps, ones, COLORS[st], 0.0
        if st == "listening":
            self._ring.append((now, self._mic.update(level, dt)))
            amps = [0.12 + 0.88 * self._delayed(now - 0.035 * abs(i - CENTER)) for i in range(N_BARS)]
            return amps, ones, COLORS[st], 0.0
        if st in ("thinking", "loading"):
            period = 1.4 if st == "thinking" else 2.0
            if self.reduced_motion:
                return [0.35] * N_BARS, ones, COLORS[st], 0.0
            p = CENTER - CENTER * math.cos(2 * math.pi * t / period)
            bumps = [math.exp(-((i - p) ** 2) / (2 * 1.1 ** 2)) for i in range(N_BARS)]
            return [0.35 + 0.55 * b for b in bumps], [0.55 + 0.45 * b for b in bumps], COLORS[st], 0.0
        if st == "speaking":
            lv = self._tts.update(level, dt)
            if self.reduced_motion:
                return [0.15 + 0.85 * lv] * N_BARS, ones, COLORS[st], 0.0
            amps = [
                (0.15 + 0.85 * lv) * (1 + 0.12 * math.sin(2 * math.pi * (1.7 + 0.14 * i) * now + 0.9 * i))
                for i in range(N_BARS)
            ]
            return amps, ones, COLORS[st], 0.0
        if st == "muted":
            return [0.0] * N_BARS, ones, COLORS[st], 0.0
        if st == "error":
            xoff = 0.0
            if not self.reduced_motion and t < SHAKE_S:
                xoff = 4 * math.sin(2 * math.pi * 3 * t / SHAKE_S) * (1 - t / SHAKE_S)
            return [0.30] * N_BARS, ones, COLORS[st], xoff
        return [0.30] * N_BARS, [0.6] * N_BARS, COLORS["off"], 0.0  # off / unknown

    def _delayed(self, when: float) -> float:
        for ts, v in reversed(self._ring):
            if ts <= when:
                return v
        return self._ring[0][1] if self._ring else 0.0


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
