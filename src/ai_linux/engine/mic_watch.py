"""Spots a microphone that sends nothing or only digital zeros (a wrong input such as S/PDIF, or unplugged)."""

from __future__ import annotations

import re
import subprocess

FLOOR = 4 / 32768  # a working mic peaks above ~50 steps even after echo cancelling; a dead input sends 0
DEAD_S = 5.0


class SilenceWatch:
    def __init__(self, dead_s: float = DEAD_S):
        self.dead_s = dead_s
        self.restart(0.0)

    def restart(self, now: float) -> None:
        self._since, self._warned = now, False

    def frame(self, peak: float | None, now: float) -> str | None:
        """One frame's peak (None: no frame came). "dead" once after dead_s, then "back" when sound returns."""
        if peak is not None and peak > FLOOR:
            self._since = now
            if self._warned:
                self._warned = False
                return "back"
            return None
        if not self._warned and now - self._since >= self.dead_s:
            self._warned = True
            return "dead"
        return None


def input_name() -> str | None:
    """The desktop's current input, as Settings names it."""
    try:
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True,
                             timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'node\.description = "([^"]*)"', out)
    return m.group(1) if m else None


def dead_text(name: str | None) -> str:
    using = f' The input in use is "{name}".' if name else ""
    return f"The microphone sends only silence, so the assistant can't hear you.{using} " \
           "Choose your microphone in Settings, Sound, Input."
