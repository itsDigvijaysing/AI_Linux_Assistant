# Skills — procedure seed

A tiny seed of SKILL-style procedures the assistant can follow. This is the
**placeholder for the Phase 6 "Memory & skills" layer**: a retriever
(keyword first, later a small hybrid RAG seeded from Fabric patterns) will pick
the relevant SKILL at runtime and feed it to the model.

For now these are **reference procedures only** — not yet wired into the loop.

**Format:** one markdown file per skill, short and imperative, with a small
front-matter block (`name`, `trigger`, `tools`).
