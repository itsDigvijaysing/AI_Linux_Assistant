"""Exports Laya typed-decisions to OpenVINO fp16 for engine/laya.py, fills the model folder, and checks the torch-free
port against Laya's own Agent (same token ids, same probabilities). Writes tests/fixtures/laya_golden.json.

Usage (AI_Linux env, which has torch): laya_export.py <checkpoint>/typed-decisions <unpacked laya-0.4.15 wheel> [out]
"""

import gc
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(Path(__file__).parent), str(ROOT / "src"), sys.argv[2]]
sys.modules.setdefault("openvino_telemetry", None)  # no Intel analytics on import

import laya  # noqa: E402
import openvino as ov  # noqa: E402
import torch  # noqa: E402
from laya import common  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

from ai_linux.engine import laya as port  # noqa: E402
from ai_linux.paths import CACHE_DIR  # noqa: E402
from routers import INSTR  # noqa: E402
from tasks import TASKS  # noqa: E402
from tools import menu  # noqa: E402

REVISION = "7b928d828b7b0e022f929d9bd2e44165aa270148"
MAX_OPTIONS = 32

ckpt = Path(sys.argv[1])
out = Path(sys.argv[3]) if len(sys.argv) > 3 else port.LAYA_DIR
out.mkdir(parents=True, exist_ok=True)
xml = out / "openvino_model.xml"
ref_cache = CACHE_DIR / "laya_ref_probs.json"  # the torch pass takes minutes; reruns reuse it
agent = None
if not (xml.exists() and ref_cache.is_file()):
    agent = laya.Agent(str(ckpt), compile=False, device="cpu")
    model = agent.model.float().eval()
if not xml.exists():
    # batch 2 so tracing keeps the batch axis symbolic (batch 1 gets baked in)
    ids = torch.randint(5, 1000, (2, 24))
    mask = torch.ones(2, 24, dtype=torch.long)
    mask[1, 20:] = 0
    mpos = torch.tensor([[1, 2, 3], [1, 2, 3]])
    mmask = torch.tensor([[True, True, True], [True, True, False]])
    with torch.no_grad():
        ovm = ov.convert_model(model, example_input=(ids, mask, mpos, mmask, torch.tensor([0, 0])))
    ov.save_model(ovm, str(xml), compress_to_fp16=True)

cfg = json.loads((ckpt / "rl_agent_config.json").read_text())
for f in ("tokenizer.json", "tokenizer_config.json"):
    shutil.copy(ckpt / "tokenizer" / f, out / f)
shutil.copy(Path(sys.argv[2]) / "laya-0.4.15.dist-info/licenses/LICENSE", out / "LICENSE")
(out / "decision_config.json").write_text(json.dumps({
    "format_version": 1, "source": "convaiinnovations/laya", "subfolder": "typed-decisions", "revision": REVISION,
    "laya_version": "0.4.15", "max_len": cfg["max_len"], "head_max_len": cfg["head_max_len"],
    "max_options": MAX_OPTIONS, "temperature": cfg["temperature"],
    "temperature_by_options": cfg["temperature_by_options"]}, indent=2) + "\n")

cases = [(text, instr, dict(opts)) for instr, opts, items in TASKS.values() for text, _ in items]
rows = [json.loads(line) for line in open(ROOT / "bench/data/eval.jsonl")][:120]
cases += [(r["text"], INSTR, menu(True)) for r in rows]
hf_tok = AutoTokenizer.from_pretrained(str(ckpt / "tokenizer"))
my_tok = port.Tok(out / "tokenizer.json")

same_seq = 0
for text, instr, opts in cases:
    q = {"t": "choice", "ins": instr, "crit": opts}
    ref = common.build_sequence(hf_tok, text, q, cfg["max_len"], cfg["head_max_len"])
    same_seq += tuple(ref) == port.build_sequence(my_tok, text, instr, opts, cfg["max_len"], cfg["head_max_len"])
print(f"token ids + markers identical: {same_seq}/{len(cases)}", flush=True)

if agent is not None:
    ref_probs = []
    for text, instr, opts in cases:
        a = agent.predict(text, {"q": {"type": "choice", "instructions": instr, "criteria": opts}})
        ref_probs.append(dict(a["answers"]["q"]["probabilities"]))
    ref_cache.parent.mkdir(parents=True, exist_ok=True)
    ref_cache.write_text(json.dumps(ref_probs))
    del agent, model
    gc.collect()  # torch's copy and an OpenVINO GPU compile don't fit in 6 GB together
ref_probs = json.loads(ref_cache.read_text())

fixture = ROOT / "tests/fixtures/laya_golden.json"
golden = []
for device in ("CPU", "GPU"):
    m = port.Laya(out, device)
    m.ask("warm up", "Is this a test?", {"yes": "", "no": ""})
    same_top, diffs, ms = 0, [], []
    for i, ((text, instr, opts), ref) in enumerate(zip(cases, ref_probs)):
        t = time.perf_counter()
        p = m.ask(text, instr, opts)
        ms.append(1000 * (time.perf_counter() - t))
        same_top += max(p, key=p.get) == max(ref, key=ref.get)
        diffs.append(max(abs(p[n] - ref[n]) for n in opts))
        if device == "CPU" and i % 12 == 0:
            ids, markers = port.build_sequence(my_tok, text, instr, opts, cfg["max_len"], cfg["head_max_len"])
            golden.append({"state": text, "question": instr, "options": opts, "ids": ids, "markers": markers,
                           "probs": ref})
    print(f"{m.device}: same top {same_top}/{len(cases)}, prob diff median {statistics.median(diffs):.4f} "
          f"max {max(diffs):.4f}, median {statistics.median(ms):.0f} ms", flush=True)
    if device == "CPU":
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(json.dumps(golden, separators=(",", ":")) + "\n")
        print(f"{len(golden)} golden cases -> {fixture}", flush=True)
    del m
    gc.collect()
