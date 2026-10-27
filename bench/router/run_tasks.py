"""Runs one model over tasks.TASKS; writes results/tasks-<name>.jsonl (task, text, gold, top, conf, ms)."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tasks import TASKS  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


def run(model, name):
    with open(BASE / f"results/tasks-{name}.jsonl", "w") as f:
        for task, (instr, options, items) in TASKS.items():
            model.ask("warm up", options, instr)
            for text, gold in items:
                t = time.perf_counter()
                p = model.ask(text, options, instr)
                ms = 1000 * (time.perf_counter() - t)
                top = max(p, key=p.get)
                f.write(json.dumps({"task": task, "text": text, "gold": gold, "top": top, "conf": p[top], "ms": ms}) + "\n")
            print(name, task, "done", flush=True)
