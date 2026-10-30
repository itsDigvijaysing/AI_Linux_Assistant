#!/usr/bin/env python3
"""Maintainer tool (not run by the app): write the top-bar icons to src/ai_linux/data/icons/.

Symbolic, so the shell paints them in the panel's own text colour (white on a dark bar, black on a light one):
solid while Phoenix is at work, faint while it waits in the background, flat dots when stopped.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_linux.tray import FAINT, WAVE_FRAMES  # noqa: E402
from ai_linux.ui.bar_model import TAPER  # noqa: E402

OUT = ROOT / "src/ai_linux/data/icons"


def svg(heights: list[float], opacity: float = 1.0) -> str:
    bars = []
    for i, h in enumerate(heights):
        hh = max(2.0, round(12 * h * 2) / 2)
        bars.append(f'    <rect x="{i * 1.5:g}" y="{(16 - hh) / 2:g}" width="1" height="{hh:g}" rx="0.5"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">\n'
            f'  <g fill="#bebebe" opacity="{opacity:g}">\n' + "\n".join(bars) + "\n  </g>\n</svg>\n")


def main() -> int:
    icons = {"phoenix-tray-active": svg(list(TAPER)), "phoenix-tray-idle": svg(list(TAPER), FAINT),
             "phoenix-tray-muted": svg([0.0] * len(TAPER), FAINT)}
    for f in range(WAVE_FRAMES):
        phase = 2 * math.pi * f / WAVE_FRAMES
        icons[f"phoenix-tray-wave{f}"] = svg([t * (0.7 + 0.3 * math.sin(phase - i * 0.8)) for i, t in enumerate(TAPER)])
    for name, text in icons.items():
        (OUT / f"{name}-symbolic.svg").write_text(text)
        print("wrote", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
