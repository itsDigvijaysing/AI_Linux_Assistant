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
import subprocess

from loguru import logger
from mcp.server.fastmcp import FastMCP

logger.remove()
logging.getLogger().setLevel(logging.CRITICAL)

mcp = FastMCP("shell")

_MAX_OUTPUT = 8000  # chars of stdout/stderr returned to the model
_COMMAND_TIMEOUT = 20.0  # fixed; kept under the 30s MCP tool_timeout so the child self-kills first


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
