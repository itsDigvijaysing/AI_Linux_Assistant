"""Action safety gate (AI_Linux v3, GTK-free).

Decides whether a tool call may run. Ported from src/glados/core/tool_safety.py, with the env-var
policy (GLADOS_ALLOW_ACTIONS / GLADOS_CONFIRM_TOOLS) replaced by an explicit ``ActionGate`` config
object, so the app wires it from Settings and tests construct it directly.

Model (unchanged in spirit, see SECURITY.md):
  * Gated families take irreversible real-world actions: ``shell.run_command`` (arbitrary shell),
    ``desktop.*`` (the typed desktop tools), ``computer_use.*`` (GUI control) and ``mcp.*`` (the user's
    MCP servers; each server also asks before every call unless the user turned that off).
  * A non-gated tool is always allowed.
  * In autonomy mode a gated tool is ALWAYS denied (hard floor), whatever the globs or arming.
  * Otherwise a gated tool runs only when the session is ARMED; ``confirm_globs`` names the subset
    that additionally needs an interactive yes/no before running (the UI shows an Adw.AlertDialog).

The gate only DECIDES; it never prompts. The executor calls a ``ConfirmHook`` when a Decision says
``needs_confirm``. The hook is async (it takes a callback) so the GTK UI can present a dialog
without blocking; ``sync_confirm`` adapts it for the worker thread the executor runs on.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Families that take real actions (new v3 tool names). Kept identical for gating and autonomy deny.
GATED_FAMILIES: tuple[str, ...] = ("shell.run_command", "desktop.*", "computer_use.*", "mcp.*")


@dataclass(frozen=True)
class Decision:
    """Outcome of a gate check."""

    allowed: bool
    needs_confirm: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ConfirmRequest:
    """What the UI shows the user before an action that needs confirmation runs."""

    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    command: str | None = None
    detail: str = ""


# Async hook the UI implements: present a dialog for ``request`` then call ``respond(allowed)``.
ConfirmHook = Callable[[ConfirmRequest, Callable[[bool], None]], None]


def _matches(tool_name: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch.fnmatch(tool_name, p) for p in patterns)


class ActionGate:
    """Arm-based action gate with an autonomy hard floor. Thread-safe to read."""

    def __init__(self, armed: bool, confirm_globs: list[str] | None = None, autonomy: bool = False):
        self.armed = bool(armed)
        # Default: every gated family needs confirmation; an empty list means "arm, never ask".
        self.confirm_globs = list(GATED_FAMILIES) if confirm_globs is None else list(confirm_globs)
        self.autonomy = bool(autonomy)

    def is_gated(self, tool_name: str) -> bool:
        """True if the tool takes a real action and so is subject to the gate."""
        return _matches(tool_name, GATED_FAMILIES)

    def check(self, tool_name: str, args: dict[str, Any] | None = None) -> Decision:
        """Decide whether ``tool_name`` may run. Fail-safe: anything unclear is denied."""
        if not self.is_gated(tool_name):
            return Decision(allowed=True)
        if self.autonomy:  # hard floor, independent of arming and globs
            log.warning("gate: auto-denied '%s' (autonomy hard floor)", tool_name)
            return Decision(allowed=False, reason="autonomy hard floor: gated actions are never run unsupervised")
        if not self.armed:
            return Decision(allowed=False, reason="actions are disarmed; enable actions to let the assistant act")
        needs = _matches(tool_name, self.confirm_globs)
        return Decision(allowed=True, needs_confirm=needs)


def sync_confirm(hook: ConfirmHook | None, request: ConfirmRequest, timeout: float = 120.0) -> bool:
    """Call an async ``ConfirmHook`` from a worker thread and block for the user's answer.

    Returns False (deny) when no hook is wired, the hook raises, or the user does not answer in
    time: confirmation required but unobtainable always fails safe.
    """
    if hook is None:
        log.warning("gate: '%s' needs confirmation but no confirm hook is wired; denying", request.tool_name)
        return False
    done = threading.Event()
    result = {"ok": False}

    def respond(allowed: bool) -> None:
        result["ok"] = bool(allowed)
        done.set()

    try:
        hook(request, respond)
    except Exception as exc:  # any hook failure must fail safe
        log.warning("gate: confirm hook failed for '%s' (%s); denying", request.tool_name, exc)
        return False
    if not done.wait(timeout):
        log.warning("gate: confirm hook timed out for '%s'; denying", request.tool_name)
        return False
    return result["ok"]
