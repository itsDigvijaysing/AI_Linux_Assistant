"""Per-task accuracy, calibration (ECE, 10 bins) and median latency for results/tasks-*.jsonl."""

import json
import statistics
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
ORDER = ["confirm", "pick_app", "settings_page", "addressed", "feedback", "shell_risk"]


def ece(rows):
    bins = [[] for _ in range(10)]
    for r in rows:
        bins[min(9, int(r["conf"] * 10))].append(r)
    return sum(len(b) / len(rows) * abs(sum(r["top"] == r["gold"] for r in b) / len(b) - sum(r["conf"] for r in b) / len(b))
               for b in bins if b)


print(f"{'model':18s} " + " ".join(f"{t[:9]:>9s}" for t in ORDER) + f" {'overall':>8s} {'ECE':>5s} {'ms':>6s}")
for p in sorted((BASE / "results").glob("tasks-*.jsonl")):
    rows = [json.loads(line) for line in open(p)]
    acc = {t: [r["top"] == r["gold"] for r in rows if r["task"] == t] for t in ORDER}
    cells = " ".join(f"{100 * sum(a) / len(a):8.0f}%" if a else f"{'-':>9s}" for a in acc.values())
    allr = [r["top"] == r["gold"] for r in rows]
    print(f"{p.stem[6:]:18s} {cells} {100 * sum(allr) / len(allr):7.0f}% {ece(rows):5.2f} {statistics.median(r['ms'] for r in rows):6.0f}")
