# Skills — procedure seed

A tiny seed of SKILL-style procedures the assistant can follow. This is the
**placeholder for the Phase 6 "Memory & skills" layer**: a retriever
(keyword first, later a small hybrid RAG seeded from Fabric patterns) will pick
the relevant SKILL at runtime and feed it to the model.

These procedures are **live** via the `skills` MCP server (`glados.mcp.skills_server`,
registered in `configs/ai_linux_config.yaml`): `list_skills` and `find_skill` let the
model fetch the matching SKILL at runtime. Embeddings / hybrid RAG remain a future
enhancement behind the same interface.

**Format:** one markdown file per skill, short and imperative, with a small
front-matter block (`name`, `trigger`, `tools`).
