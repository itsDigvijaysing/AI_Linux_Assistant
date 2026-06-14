"""Skills retriever exposed over MCP (AI_Linux, Phase 6).

A deliberately tiny "memory & skills" layer: it reads the SKILL-*.md procedure
files from the repo's ``skills/`` directory and lets the model fetch the most
relevant one by keyword. No embeddings, no external RAG engine — the project
goal is efficiency, and keyword matching is enough for a handful of procedures.
It can grow into a real hybrid RAG (Fabric patterns + AIChat) later behind the
same interface. Conversation memory itself is handled by the ``memory`` server.

Skills dir resolution: $GLADOS_SKILLS_DIR, else <repo>/skills, else ./skills.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from loguru import logger
from mcp.server.fastmcp import FastMCP

logger.remove()
logging.getLogger().setLevel(logging.CRITICAL)

mcp = FastMCP("skills")


def _skills_dir() -> Path:
    env = os.environ.get("GLADOS_SKILLS_DIR")
    if env:
        return Path(env).expanduser()
    # this file: <repo>/src/glados/mcp/skills_server.py  -> parents[3] == <repo>
    repo_skills = Path(__file__).resolve().parents[3] / "skills"
    if repo_skills.exists():
        return repo_skills
    return Path.cwd() / "skills"


def _load_skills() -> list[dict[str, str]]:
    directory = _skills_dir()
    skills: list[dict[str, str]] = []
    if not directory.exists():
        return skills
    for path in sorted(directory.glob("SKILL-*.md")):
        text = path.read_text(encoding="utf-8")
        name = path.stem
        trigger = ""
        m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if m:
            name = m.group(1).strip()
        t = re.search(r"^trigger:\s*(.+)$", text, re.MULTILINE)
        if t:
            trigger = t.group(1).strip()
        skills.append({"name": name, "trigger": trigger, "file": path.name, "text": text})
    return skills


@mcp.tool()
def list_skills() -> str:
    """List available skill procedures (name + trigger)."""
    skills = _load_skills()
    return json.dumps([{"name": s["name"], "trigger": s["trigger"]} for s in skills])


@mcp.tool()
def find_skill(query: str) -> str:
    """Return the best-matching skill procedure for a query (keyword match)."""
    skills = _load_skills()
    if not skills:
        return json.dumps({"error": "no skills available"})
    query_words = set(re.findall(r"\w+", (query or "").lower()))

    def score(skill: dict[str, str]) -> int:
        hay = f"{skill['name']} {skill['trigger']} {skill['text']}".lower()
        return sum(1 for w in query_words if w in hay)

    best = max(skills, key=score)
    if score(best) == 0:
        return json.dumps({"match": None, "available": [s["name"] for s in skills]})
    return json.dumps({"match": best["name"], "trigger": best["trigger"], "procedure": best["text"]})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
