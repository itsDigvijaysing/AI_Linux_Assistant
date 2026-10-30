"""Live text as the engine would do it: decode the growing utterance every STEP s, from a cold start."""
import json
import sys
import time

import soundfile as sf

variant = sys.argv[2]
sys.argv = [sys.argv[0], "run", sys.argv[1]]
import asr_bench as b  # noqa: E402

STEP = 0.6

from ai_linux.engine.asr import get_transcriber, tdt_asr  # noqa: E402

orig = tdt_asr._OnnxTDTModel._init_session


def init_session(self, path, opts, providers):
    if variant == "ov-gpu" and str(path).endswith("encoder.onnx"):
        return b.OVSession(path, "GPU")
    return orig(self, path, opts, providers)


tdt_asr._OnnxTDTModel._init_session = init_session
t = time.monotonic()
tr = get_transcriber("parakeet-tdt")
print(f"load {time.monotonic() - t:.1f}s")
items = json.loads((b.WORK / "set.json").read_text())
picks = [it for it in items if it["kind"] == "noise10" and any(k in it["path"] for k in ("M1-09", "F1-03", "M1-11"))]
t_load = time.monotonic()
for offset, it in [(0.0, p) for p in picks] + [(0.27, p) for p in picks]:
    audio = sf.read(it["path"], dtype="float32")[0]
    print(f"\n[{time.monotonic() - t_load:4.1f}s after load, offset {offset}] {it['text']}")
    n = int((STEP + offset) * 16000)
    while n <= len(audio):
        c, w = time.process_time(), time.perf_counter()
        text = tr.transcribe(audio[:n])
        print(f"  {n / 16000:4.1f}s  {time.perf_counter() - w:5.3f}s wall {time.process_time() - c:5.2f}s cpu  {text}")
        n += int(STEP * 16000)
