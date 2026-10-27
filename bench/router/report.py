"""Scores results/*.jsonl. The confidence threshold is fitted on the dev split (false actions <= 2 %) and
reported on the test split. Below the threshold, or on 'none', the request goes to the big model."""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tools import NEW  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
MAX_FALSE = 0.02


def metrics(rows, thr):
    acts = [r for r in rows if r["top"] != "none" and r["conf"] >= thr]
    false = sum(r["top"] != r["gold"] for r in acts)
    cmds = [r for r in rows if r["gold"] != "none"]
    good = sum(r["top"] == r["gold"] for r in acts)
    return {"false": false / len(rows), "coverage": good / max(1, len(cmds))}


def fit(dev):
    best = None
    for thr in sorted({round(r["conf"], 4) for r in dev} | {0.0, 1.01}):
        m = metrics(dev, thr)
        if m["false"] <= MAX_FALSE and (best is None or m["coverage"] > best[1]["coverage"]):
            best = (thr, m)
    return best[0] if best else 1.01


def report(path):
    rows = [json.loads(line) for line in open(path)]
    out = {}
    for menu_size in (16, 26):
        rs = [r for r in rows if r["menu"] == menu_size]
        if not rs:
            continue
        dev, test = [r for r in rs if r["split"] == "dev"], [r for r in rs if r["split"] == "test"]
        thr = fit(dev)
        m = metrics(test, thr)
        top1 = sum(r["top"] == r["gold"] for r in test) / len(test)
        nones = [r for r in test if r["gold"] == "none"]
        new = [r for r in test if r["gold"] in NEW]
        hand = [r for r in test if r["source"] == "handwritten"]
        human = [r for r in test if r["source"] != "handwritten"]
        out[menu_size] = {
            "top1": round(top1, 3), "thr": round(thr, 3), "false_action": round(m["false"], 3),
            "handled_locally": round(m["coverage"], 3),
            "none_detected": round(sum(r["top"] == "none" or r["conf"] < thr for r in nones) / max(1, len(nones)), 3),
            "new_tools_top1": round(sum(r["top"] == r["gold"] for r in new) / len(new), 3) if new else None,
            "top1_handwritten": round(sum(r["top"] == r["gold"] for r in hand) / max(1, len(hand)), 3),
            "top1_human_datasets": round(sum(r["top"] == r["gold"] for r in human) / max(1, len(human)), 3),
            "median_ms": round(statistics.median(r["ms"] for r in rs)), "n_test": len(test)}
    return out


if __name__ == "__main__":
    for p in sorted((BASE / "results").glob(sys.argv[1] if len(sys.argv) > 1 else "*.jsonl")):
        if p.stem.startswith("tasks-"):  # other-decision results, scored by report_tasks.py
            continue
        for k, v in report(p).items():
            print(f"{p.stem:32s} menu{k}: {v}")
