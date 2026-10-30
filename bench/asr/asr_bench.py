"""Parakeet TDT 0.6B v3 variants through our own AudioTranscriber: accuracy (WER) and speed on this laptop.

Run from the repo root with the engine venv, PYTHONPATH=src plus a folder holding `onnx`, under the 6 GB scope:
    asr_bench.py make                 build the test set (SuperTonic, 2 voices, 4 conditions)
    asr_bench.py run <variant>        fp32-cpu (the current FP16 model) | int8-cpu | int8enc-cpu | ov-gpu | ov-cpu
BUCKET=100 pads encoder input to whole seconds. OCL_ICD_VENDORS=/etc/OpenCL/vendors/intel.icd keeps NVIDIA asleep.
"""
import json
import os
import re
import resource
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.modules["openvino_telemetry"] = None
WORK = Path(os.path.expanduser("~/.cache/ai-linux/asr-bench"))
ASR = Path("models/ASR")
SENTENCES = [
    "Phoenix, open the files app.",
    "Set the volume to thirty percent.",
    "What is the weather like in Pune tomorrow morning?",
    "Write a reply saying I will join the meeting at half past five.",
    "Turn down the screen brightness a little.",
    "Remind me to call the bank on Friday.",
    "Please summarize this article in three short bullet points.",
    "Open a new terminal window and show my battery health.",
    "Can you play some relaxing music on Spotify?",
    "The quarterly report shows revenue grew by twelve percent compared to last year.",
    "Search the web for the latest Ubuntu release notes.",
    "Take a screenshot of the current window and save it to my pictures folder.",
]


def resample(x, sr_in, sr_out):
    n = int(round(len(x) * sr_out / sr_in))
    spec = np.fft.rfft(x)
    out = np.zeros(n // 2 + 1, complex)
    m = min(len(spec), len(out))
    out[:m] = spec[:m]
    return (np.fft.irfft(out, n) * (n / len(x))).astype(np.float32)


def mix(speech, other, snr_db):
    p = np.mean(speech[np.abs(speech) > 1e-4] ** 2)
    other = np.resize(other, len(speech))
    return (speech + other * np.sqrt(p / (np.mean(other ** 2) * 10 ** (snr_db / 10)))).astype(np.float32)


def make():
    from ai_linux.engine.tts import SpeechSynthesizer
    rng = np.random.default_rng(7)
    clips = {}
    for voice in ("M1", "F1"):
        tts = SpeechSynthesizer(voice=voice)
        for i, text in enumerate(SENTENCES):
            audio = resample(tts.generate_speech_audio(text, speed=1.05), tts.sample_rate, 16000)
            clips[voice, i] = np.concatenate([np.zeros(4000, np.float32), audio, np.zeros(4000, np.float32)])
    items = []
    for (voice, i), audio in clips.items():
        other = clips["F1" if voice == "M1" else "M1", (i + 5) % len(SENTENCES)]
        kinds = {"clean": audio, "noise10": mix(audio, rng.normal(size=len(audio)), 10),
                 "noise0": mix(audio, rng.normal(size=len(audio)), 0), "babble10": mix(audio, other, 10)}
        for kind, a in kinds.items():
            path = WORK / "set" / f"{voice}-{i:02d}-{kind}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, a, 16000)
            items.append({"path": str(path), "text": SENTENCES[i], "kind": kind, "secs": len(a) / 16000})
    (WORK / "set.json").write_text(json.dumps(items, indent=1))
    print(len(items), "clips")


NUMS = {"thirty": "30", "twelve": "12", "five": "5", "three": "3"}


def words(text):
    return [NUMS.get(w, w) for w in re.findall(r"[a-z0-9']+", text.lower().replace("%", " percent"))]


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


class _Meta:
    def __init__(self, path):
        import onnx
        m = onnx.load(str(path), load_external_data=False)
        self.custom_metadata_map = {p.key: p.value for p in m.metadata_props}


class _Port:
    def __init__(self, name, shape=None):
        self.name, self.shape, self.type = name, shape, "tensor(float)"


class OVSession:
    """Just enough of an onnxruntime session for _OnnxTDTModel."""

    def __init__(self, path, device, precision=None):
        import openvino as ov
        core = ov.Core()
        core.set_property({"CACHE_DIR": str(WORK / "ov-cache")})
        cfg = {"PERFORMANCE_HINT": "LATENCY"}
        if precision:
            cfg["INFERENCE_PRECISION_HINT"] = precision
        t = time.monotonic()
        self.compiled = core.compile_model(str(path), device, cfg)
        self.compile_s = time.monotonic() - t
        self.req = self.compiled.create_infer_request()
        self.meta = _Meta(path)

    def get_inputs(self):
        return [_Port(i.any_name) for i in self.compiled.inputs]

    def get_outputs(self):
        return [_Port(o.any_name) for o in self.compiled.outputs]

    def get_modelmeta(self):
        return self.meta

    def run(self, names, feed):
        res = self.req.infer(feed)
        return [res[o] for o in self.compiled.outputs] if names is None else [res[self.compiled.output(0)]]


BUCKET = int(os.environ.get("BUCKET", "0"))  # feature frames (10 ms each); 0 = no padding


def bucketed_encoder(self, features):
    """Pad the time axis up to a multiple of BUCKET so the GPU sees few shapes; keep the true length."""
    t = features.shape[2]
    pad = -(-t // BUCKET) * BUCKET - t
    feed = {self.encoder_in_names[0]: np.pad(features, ((0, 0), (0, 0), (0, pad))),
            self.encoder_in_names[1]: np.array([t], np.int64)}
    out, lens = self.encoder.run(None, feed)[:2]
    return np.asarray(out[:, :, :int(np.asarray(lens).ravel()[0])], np.float32)


def ort_session(path):
    import onnxruntime as ort
    from ai_linux.engine.onnx_opts import session_options, threads
    return ort.InferenceSession(str(path), sess_options=session_options(threads(4)), providers=["CPUExecutionProvider"])


def run(variant):
    from ai_linux.engine.asr import get_transcriber, tdt_asr
    extra = {}
    original = tdt_asr._OnnxTDTModel._init_session

    def init_session(self, path, opts, providers):  # swap one part without loading the original first
        part = Path(path).name.split("_")[-1].removesuffix(".onnx")
        if variant.startswith("ov-") and part == "encoder":
            if variant == "ov-gpu-int8":
                path = WORK / "parakeet-tdt-0.6b-v3_encoder.int8.onnx"
            sess = OVSession(path, "CPU" if variant == "ov-cpu" else "GPU",
                             "f32" if variant == "ov-gpu-f32" else None)
            extra["compile_s"] = round(sess.compile_s, 1)
            return sess
        if variant == "int8-cpu" or (variant == "int8enc-cpu" and part == "encoder"):
            path = WORK / f"parakeet-tdt-0.6b-v3_{part}.int8.onnx"
        return original(self, path, opts, providers)

    tdt_asr._OnnxTDTModel._init_session = init_session
    if BUCKET:
        tdt_asr._OnnxTDTModel.run_encoder = bucketed_encoder
    label = f"{variant}-b{BUCKET}" if BUCKET else variant
    t0 = time.monotonic()
    tr = get_transcriber("parakeet-tdt")
    if variant.startswith("ov-"):
        links = [os.readlink(f"/proc/self/fd/{f}") for f in os.listdir("/proc/self/fd")
                 if os.path.islink(f"/proc/self/fd/{f}")]
        extra["dev_fds"] = sorted({p for p in links if p.startswith("/dev/")})
    load_s = time.monotonic() - t0
    items = json.loads((WORK / "set.json").read_text())
    tr.transcribe(sf.read(items[0]["path"], dtype="float32")[0])  # warm-up
    errs = {}
    hyps = []
    rtf, cpu, wall, misses, first = [], [], [], [], []
    for it in items:
        audio = sf.read(it["path"], dtype="float32")[0]
        times = []
        for _ in range(2):
            c, w = time.process_time(), time.perf_counter()
            text = tr.transcribe(audio)
            times.append((time.perf_counter() - w, time.process_time() - c))
        first.append(times[0][0])
        w_s, c_s = min(times)
        e, n = wer(it["text"], text)
        tot = errs.setdefault(it["kind"], [0, 0])
        tot[0] += e
        tot[1] += n
        hyps.append(text)
        wall.append(w_s)
        cpu.append(c_s)
        rtf.append(w_s / it["secs"])
        if e:
            misses.append({"kind": it["kind"], "ref": it["text"], "hyp": text})
    for f in os.listdir("/proc/self/fd"):
        if os.path.realpath(f"/proc/self/fd/{f}").startswith("/dev/dri/render"):
            info = open(f"/proc/self/fdinfo/{f}").read()
            extra["gpu_mem"] = re.findall(r"drm-(?:total|resident)-[\w-]+:\s+\d+ \w+", info)
            break
    out = {"variant": label, "load_s": round(load_s, 1), **extra,
           "wer": {k: round(100 * e / n, 2) for k, (e, n) in errs.items()},
           "median_s": round(statistics.median(wall), 3), "first_median_s": round(statistics.median(first), 3),
           "first_max_s": round(max(first), 3), "median_cpu_s": round(statistics.median(cpu), 3),
           "rtf": round(statistics.median(rtf), 3),
           "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
           "rss_mb": int(re.search(r"VmRSS:\s+(\d+)", open("/proc/self/status").read()).group(1)) // 1024,
           "misses": misses, "hyps": hyps}
    (WORK / f"result-{label}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("misses", "hyps")}))


if __name__ == "__main__":
    make() if sys.argv[1] == "make" else run(sys.argv[2])
