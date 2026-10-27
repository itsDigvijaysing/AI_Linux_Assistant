"""Runs one router over data/eval.jsonl with both menus (16 tools, 26 tools) and writes results/<name>.jsonl."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tools import gold_for, menu  # noqa: E402

BASE = Path(__file__).resolve().parents[1]


def run(router, limit=None, menus=(False, True)):
    rows = [json.loads(line) for line in open(BASE / "data/eval.jsonl")][:limit]
    out = BASE / f"results/{router.name}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        for with_new in menus:
            m = menu(with_new)
            router.scores("warm up", m)
            for i, r in enumerate(rows):
                t = time.perf_counter()
                p = router.scores(r["text"], m)
                ms = 1000 * (time.perf_counter() - t)
                ranked = sorted(p.items(), key=lambda kv: -kv[1])
                f.write(json.dumps({**r, "menu": 26 if with_new else 16, "gold": gold_for(r["label"], with_new),
                                    "top": ranked[0][0], "conf": ranked[0][1],
                                    "second": ranked[1][1] if len(ranked) > 1 else 0.0, "ms": ms}) + "\n")
                if i % 100 == 0:
                    print(f"{router.name} menu{26 if with_new else 16} {i}/{len(rows)} {ms:.0f} ms", flush=True)
    return out
