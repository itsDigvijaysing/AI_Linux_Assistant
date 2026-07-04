# Skills — procedure library

One markdown file per desktop procedure, short and imperative, with a small
front-matter block (`name`, `trigger`, `tools`).

**Role since the native-tools pivot (commit 305a97c):** the default runtime does NOT
retrieve these files per turn. Each desktop capability is a typed, named
function-calling tool in `glados.mcp.skills_actions_server` (gated, executed through the
shared denylisted `shell_exec.run_shell`); the commands in these SKILL files are the
curated source those tools were built from, and this library remains their reference
documentation.

The files are still consumed at runtime by:

- the **optional** `skills` MCP server (`glados.mcp.skills_server`: `list_skills` /
  `find_skill` — commented out in `configs/ai_linux_config.yaml`; re-enable to get
  keyword/hybrid retrieval over this library again),
- `/learn` (writes new drafts to `skills/learned/` via `skills_writer`), and
- `/tidy` (catalog + feedback review).

When a command here changes (or a capability is added/removed), update the matching
typed tool in `skills_actions_server.py` — the SKILL file alone no longer changes
runtime behavior.
