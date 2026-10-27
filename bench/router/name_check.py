"""Which assistant names does our own speech recognition hear? SuperTonic reads the name-test sentences in four
voices, Parakeet transcribes them, and the app's wake matcher (ai_linux.wake) scores them. Also counts false wakes
on the 771 ordinary commands in data/eval.jsonl, which never contain the name. Usage: name_check.py [name ...]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from ai_linux import wake  # noqa: E402
from ai_linux.engine.asr import get_transcriber  # noqa: E402
from ai_linux.engine.tts import SpeechSynthesizer  # noqa: E402

NAMES = sys.argv[1:] or ["phoenix", "dragon", "orion", "atlas", "griffin", "pegasus", "zephyr", "sphinx", "kraken",
                         "apollo", "hermes", "odin", "nova", "titan", "computer", "jarvis"]
VOICES = ("M1", "F1", "M3", "F3")


def to16k(audio, rate):
    n = int(len(audio) * 16000 / rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


texts = [json.loads(line)["text"] for line in open(ROOT / "bench/data/eval.jsonl")]
asr = get_transcriber("parakeet-tdt")
tts = SpeechSynthesizer(VOICES[0])
for name in NAMES:
    hits, total, missed = 0, 0, []
    for voice in VOICES:
        tts.set_voice(voice)
        for sentence in wake.phrases(name) + [wake.NEGATIVE]:
            text = asr.transcribe(to16k(tts.generate_speech_audio(sentence), tts.sample_rate))
            if sentence == wake.NEGATIVE:
                hits -= wake.heard(text, name)  # a false alarm costs a point
                continue
            total += 1
            if wake.heard(text, name):
                hits += 1
            else:
                missed.append(text)
    false = sum(wake.heard(t, name) for t in texts)
    print(f"{name:10s} heard {hits:2d}/{total}  false wakes {false}/{len(texts)}  missed: {missed[:3]}", flush=True)
