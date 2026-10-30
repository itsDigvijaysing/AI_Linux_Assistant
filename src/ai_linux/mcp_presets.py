"""Ready-made MCP servers for the Tools page: they fill in the Add Server form, which the user can still edit.

Commands and tool names are from each server's README in github.com/modelcontextprotocol/servers (read
2026-10-30). Only read-only tools start on: every tool joins the model's routing menu, and a longer menu
routes worse (bench/README.md: jiwo 89.6 % with 16 tools, 84.5 % with 26). No GTK, safe to import anywhere.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    blurb: str
    command: str
    args: tuple[str, ...]
    needs: str  # what must be installed for the command, shown when it is missing
    off: tuple[str, ...] = ()  # tools that start turned off
    env: dict = field(default_factory=dict)

    def available(self) -> bool:
        return shutil.which(self.command) is not None

    def server(self, home: Path | None = None) -> dict:
        """A server entry like the Add Server dialog saves, with ~ written out (no shell expands it)."""
        home = home or Path.home()
        args = [str(home / a[2:]) if a.startswith("~/") else str(home) if a == "~" else a for a in self.args]
        out = {"name": self.key, "command": self.command, "args": args, "enabled": True, "transport": "stdio",
               "confirm": True, "disabled_tools": list(self.off)}
        if self.env:
            out["env"] = dict(self.env)
        return out


PRESETS: tuple[Preset, ...] = (
    Preset("files", "Files", "Read and search the files in one folder (Documents; change it in Arguments). "
           "Writing, moving and editing start off.",
           "npx", ("-y", "@modelcontextprotocol/server-filesystem", "~/Documents"), "Node.js (npx)",
           off=("read_media_file", "read_multiple_files", "write_file", "edit_file", "create_directory",
                "list_directory_with_sizes", "move_file", "directory_tree", "list_allowed_directories")),
    Preset("git", "Git", "Status, log, branches and changes of one repository (set its folder in Arguments). "
           "Commit, add, reset and checkout start off.",
           "uvx", ("mcp-server-git", "--repository", "~"), "uv (uvx)",
           off=("git_diff_staged", "git_diff", "git_commit", "git_add", "git_reset", "git_create_branch",
                "git_checkout", "git_show")),
)


def get(key: str) -> Preset | None:
    return next((p for p in PRESETS if p.key == key), None)
