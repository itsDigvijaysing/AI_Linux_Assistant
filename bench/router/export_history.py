"""Exports the requests worth reviewing from history.db: the ones marked "that's wrong" and the unsure ones.

Writes bench/data/review.jsonl with the eval.jsonl fields (text, label, source, split) plus what happened.
"label" starts as what Phoenix chose; correct it by hand, then move checked rows into eval.jsonl so every
routing change is measured on real requests. Usage: python3 bench/router/export_history.py [--below 0.85]
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ai-linux/history.db"
OUT = Path(__file__).resolve().parents[1] / "data/review.jsonl"


def rows(db: Path, below: float) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = []
    for ts, heard, reply, route, model, conf, status, data in con.execute(
            "SELECT ts, heard, reply, route, model, confidence, status, data FROM turns ORDER BY ts"):
        d = json.loads(data or "{}")
        unsure = route != "rule" and conf is not None and conf < below
        if status != "wrong" and not unsure:
            continue
        tools = d.get("tools") or []
        out.append({"text": heard, "label": tools[0]["name"] if tools else "none", "source": "history",
                    "split": "review", "status": status, "confidence": conf, "alts": d.get("alts") or {},
                    "route": route, "model": model, "args": tools[0].get("args") if tools else None,
                    "reply": reply, "ts": ts})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--below", type=float, default=0.85, help="unsure: router confidence under this")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()
    found = rows(a.db, a.below)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in found))
    wrong = sum(r["status"] == "wrong" for r in found)
    print(f"{len(found)} requests ({wrong} marked wrong) -> {OUT}")


if __name__ == "__main__":
    main()
