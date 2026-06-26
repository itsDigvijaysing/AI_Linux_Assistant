"""Local shell/command executor exposed over MCP (AI_Linux, Phase 5).

This is the assistant's "executor" for regular system tasks — running a command
on the local machine. It is intentionally lean (a thin subprocess wrapper) rather
than a heavyweight agent framework: the project goal is simple/regular tasks, not
complex autonomous agent work.

SAFETY: every call to ``mcp.shell.*`` is gated (see ``glados.core.tool_safety``):
denied unless the session is armed (GLADOS_ALLOW_ACTIONS=1), and always denied in
autonomy mode. For genuinely complex,
multi-step tasks later, a heavier executor (gptme / OpenCode) can replace this
server behind the same MCP interface.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess

from loguru import logger
from mcp.server.fastmcp import FastMCP

logger.remove()
logging.getLogger().setLevel(logging.CRITICAL)

mcp = FastMCP("shell")

_MAX_OUTPUT = 8000  # chars of stdout/stderr returned to the model
_COMMAND_TIMEOUT = 20.0  # fixed; kept under the 30s MCP tool_timeout so the child self-kills first

# --- Destructive-command denylist (safety BACKSTOP, not a sandbox) ---
# The action gate already governs WHETHER shell runs; this stops the obvious data/disk-destruction forms
# a confused or prompt-injected model might emit, regardless of how the command was produced (model,
# skill, or learned skill — this is the single execution chokepoint). It's conservative: catastrophic
# top-level targets only, near-zero false positives. Not exhaustive against a determined adversary.
_HOME = os.path.expanduser("~")
_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:")
_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"--no-preserve-root"), "rm --no-preserve-root"),
    (re.compile(r"\bdd\b[^|;&\n]*\bof=/dev/(sd|nvme|mmcblk|vd|hd|disk)", re.I), "dd to a raw disk device"),
    (re.compile(r"\bmkfs(\.\w+)?\b[^|;&\n]*\s/dev/", re.I), "mkfs on a device"),
    (re.compile(r"\bwipefs\b", re.I), "wipefs"),
    (re.compile(r"\bshred\b[^|;&\n]*\s/dev/", re.I), "shred a device"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|vd|hd)", re.I), "redirect to a raw disk device"),
    (re.compile(r"\bchmod\b\s+-\S*[Rr]\S*\s+(777|000)\s+/(\s|$)"), "recursive chmod of /"),
    (re.compile(r"\bchown\b\s+-\S*[Rr]\S*\s+\S+\s+/(\s|$)"), "recursive chown of /"),
    (re.compile(r"\b(curl|wget)\b[^|;&\n]*\|\s*(sudo\s+)?(sh|bash|zsh|dash)\b", re.I), "pipe a download into a shell"),
    (_FORK_BOMB, "fork bomb"),
]


def _destructive_reason(command: str) -> str | None:
    """Return a reason string if the command matches a catastrophic pattern, else None."""
    c = " ".join(command.split())  # normalize whitespace
    for pattern, reason in _DENY:
        if pattern.search(c):
            return reason
    # rm with recursive+force flags AND a top-level target (/, ~, $HOME, /home, the home dir) — but NOT
    # a subfolder like ~/Downloads (those stay allowed).
    m = re.search(r"\brm\b(.*)", c)
    if m:
        rm_args = re.split(r"[;&|]", m.group(1))[0]  # only THIS rm's args (stop at a command separator)
        flags = "".join(re.findall(r"(?:^|\s)-([A-Za-z]+)", rm_args)).lower()
        if "r" in flags and "f" in flags:
            home = re.escape(_HOME)
            roots = rf"(?:^|\s)(?:/|~|~/|\$HOME|\$\{{HOME\}}|/home|/home/|{home}|{home}/)(?:\s|$)"
            globs = rf"(?:^|\s)(?:/\*|~/\*|/home/\*|{home}/\*)(?:\s|$)"
            if re.search(roots, rm_args) or re.search(globs, rm_args):
                return "recursive force-delete of a top-level path"
            # bare *, ., ./ wipe the current dir — and the shell's cwd is the user's home. Block UNLESS a
            # `cd` earlier in the command moved into a subfolder (then it's a scoped clear, allowed).
            if re.search(r"(?:^|\s)(?:\*|\.|\./)(?:\s|$)", rm_args) and not re.search(r"\bcd\s", c[: m.start()]):
                return "recursive force-delete of the home directory contents"
    return None


@mcp.tool()
def run_command(command: str) -> str:
    """Run a shell command on the local machine and return its result.

    Use for regular system tasks (listing files, checking status, opening things,
    small edits). Gated by the action safety gate (runs only when actions are armed).

    Returns JSON: {returncode, stdout, stderr} or {error}. Output is truncated.
    """
    command = (command or "").strip()
    if not command:
        return json.dumps({"error": "empty command"})
    reason = _destructive_reason(command)
    if reason is not None:
        logger.warning("shell: refused destructive command ({}): {}", reason, command[:120])
        return json.dumps({"error": f"refused: destructive command blocked by the safety denylist ({reason})"})
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
            cwd=os.path.expanduser("~"),
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"command timed out after {_COMMAND_TIMEOUT}s"})
    except Exception as exc:  # noqa: BLE001 - report any spawn failure to the model
        return json.dumps({"error": f"failed to run command: {exc}"})
    return json.dumps(
        {
            "returncode": proc.returncode,
            "stdout": proc.stdout[-_MAX_OUTPUT:],
            "stderr": proc.stderr[-_MAX_OUTPUT:],
        }
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
