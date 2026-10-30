#!/usr/bin/env python3
"""Clone or refresh the reference projects in refs/ (git-ignored; read their code, never run it).

    scripts/sync_refs.py [--dry-run] [name ...]

The list is ai_linux.credits (entries with a git URL). Shallow clones, no tags, no LFS files. A clone with local
edits is skipped, never reset.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_linux.credits import CREDITS, Credit, ref_dir  # noqa: E402

REFS = ROOT / "refs"
ENV = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}  # no weights, no password prompts


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=900, env=ENV)


def head(path: Path) -> str:
    return git("log", "-1", "--format=%h %cs", cwd=path).stdout.strip()


def action(dest: Path) -> str:
    """clone | update | skip"""
    if not (dest / ".git").is_dir():
        return "clone"
    return "skip" if git("status", "--porcelain", cwd=dest).stdout.strip() else "update"


def sync(credit: Credit, refs: Path = REFS, dry: bool = False) -> str:
    dest = refs / ref_dir(credit)
    todo = action(dest)
    if todo == "skip":
        return "skipped: local edits"
    if dry:
        return f"would {todo}" + (f" from {head(dest)}" if todo == "update" else "")
    if todo == "clone":
        res = git("clone", "--depth", "1", "--no-tags", credit.git, str(dest))
        return f"cloned {head(dest)}" if res.returncode == 0 else f"clone failed: {res.stderr.strip()[-200:]}"
    before = head(dest)
    res = git("fetch", "--depth", "1", "--no-tags", "origin", "HEAD", cwd=dest)
    if res.returncode:
        return f"fetch failed: {res.stderr.strip()[-200:]}"
    git("reset", "--hard", "-q", "FETCH_HEAD", cwd=dest)
    after = head(dest)
    return "up to date" if after == before else f"{before} -> {after}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("names", nargs="*", help="only these (folder names under refs/)")
    args = parser.parse_args(argv)
    todo = [c for c in CREDITS if c.git and (not args.names or ref_dir(c) in args.names)]
    REFS.mkdir(exist_ok=True)
    failed = 0
    for credit in todo:
        result = sync(credit, dry=args.dry_run)
        failed += "failed" in result
        print(f"{ref_dir(credit):24} {result}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
