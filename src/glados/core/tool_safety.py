"""Confirm-before-execute safety gate for tool calls.

AI_Linux addition. Some tools — notably desktop-control tools exposed by
computer-use-linux (``mcp.computer_use.*``) — can take irreversible real-world
actions (clicks, keystrokes, window changes). This module decides whether a tool
call must be confirmed by the user before it runs, and obtains that confirmation.

Configuration:
    GLADOS_CONFIRM_TOOLS  Comma-separated fnmatch globs of tool names that require
                          confirmation. Defaults to ``mcp.computer_use.*``.
                          Set to an empty string to disable all confirmation.
"""

import fnmatch
import os
import sys
from collections.abc import Callable
from typing import Any

from loguru import logger

_DEFAULT_CONFIRM_PATTERNS: tuple[str, ...] = ("mcp.computer_use.*", "mcp.shell.*")


def _confirm_patterns() -> list[str]:
    """Tool-name globs that require confirmation (env-overridable)."""
    raw = os.environ.get("GLADOS_CONFIRM_TOOLS")
    if raw is None:
        return list(_DEFAULT_CONFIRM_PATTERNS)
    return [p.strip() for p in raw.split(",") if p.strip()]


def requires_confirmation(tool_name: str) -> bool:
    """True if this tool must be confirmed before execution."""
    return any(fnmatch.fnmatch(tool_name, pat) for pat in _confirm_patterns())


def confirm_tool_call(
    tool_name: str,
    args: dict[str, Any],
    *,
    autonomy_mode: bool,
    prompt_fn: Callable[[str], str] | None = None,
) -> bool:
    """Return True if the tool call is approved to run.

    - In autonomy mode, gated tools are auto-DENIED: an autonomous loop must never
      fire irreversible desktop actions without a human in the loop.
    - Otherwise ask on the terminal (y/N). With no interactive TTY and no
      ``prompt_fn``, DENY (fail safe).
    """
    if not requires_confirmation(tool_name):
        return True
    if autonomy_mode:
        logger.warning("tool_safety: auto-denied '{}' (autonomy mode)", tool_name)
        return False
    question = f"[confirm] Allow {tool_name} with args {args}? [y/N] "
    try:
        if prompt_fn is not None:
            answer = prompt_fn(question)
        elif sys.stdin is not None and sys.stdin.isatty():
            answer = input(question)
        else:
            logger.warning("tool_safety: no interactive terminal; denying '{}'", tool_name)
            return False
    except (EOFError, KeyboardInterrupt):
        return False
    approved = answer.strip().lower() in ("y", "yes")
    logger.info("tool_safety: '{}' {}", tool_name, "approved" if approved else "denied")
    return approved
