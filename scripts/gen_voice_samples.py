#!/usr/bin/env python3
"""Maintainer tool (not run by the app): render every SuperTonic voice's preview for the Voice page.

Writes src/ai_linux/data/samples/<voice>.ogg. Run in the engine venv (it has SuperTonic) with ffmpeg on PATH:
    ~/.local/share/ai-linux/venv/bin/python scripts/gen_voice_samples.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_linux.engine.tts import _KNOWN_VOICES, SpeechSynthesizer  # noqa: E402

PHRASE = "Hi, I'm Phoenix. Tell me what you need, and I'll take care of it."
OUT_DIR = ROOT / "src/ai_linux/data/samples"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tts = SpeechSynthesizer(_KNOWN_VOICES[0])
    for vid in _KNOWN_VOICES:
        tts.set_voice(vid)
        audio = np.clip(np.asarray(tts.generate_speech_audio(PHRASE), dtype="float32"), -1.0, 1.0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = Path(tf.name)
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(tts.sample_rate))
            w.writeframes((audio * 32767.0).astype("<i2").tobytes())
        out = OUT_DIR / f"{vid}.ogg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "libvorbis", "-q:a", "3",
                        str(out)], check=True)
        wav_path.unlink(missing_ok=True)
        print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
