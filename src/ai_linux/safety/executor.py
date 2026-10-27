"""Gated command executor (AI_Linux v3).

The single chokepoint every tool runs through, so the denylist, the action gate and the kernel
resource caps can never be skipped. Ported from src/glados/mcp/shell_exec.py::run_shell, with the
gate made explicit (an ``ActionGate`` instance) instead of env vars, and an argv path and a
desktop-app launcher added for the typed tools.

Threading: these are blocking (subprocess) calls. Run them on a worker thread, never the GTK main
thread, and marshal results back with GLib.idle_add. ``confirm`` is a synchronous ``bool`` callback
used only when the gate says a call needs confirmation; wrap an async UI hook with
``gate.sync_confirm`` to get one.

Resource caps: commands run inside a transient ``systemd-run --user --scope`` with MemoryMax and
TasksMax (kernel-enforced containment of fork bombs and runaway memory that a regex cannot match),
with a plain-subprocess fallback when systemd-run is unavailable. See SECURITY.md for why there is
no filesystem sandbox.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

from ai_linux.safety.denylist import destructive_reason
from ai_linux.safety.gate import ActionGate, ConfirmRequest, Decision

log = logging.getLogger(__name__)

_HOME = os.path.expanduser("~")
_MAX_OUTPUT = 8000  # chars of stdout/stderr returned to the model
_DEFAULT_TIMEOUT = 20.0  # kept under the engine's tool timeout so the child self-kills first

# Validated once with exactly the props run_shell uses, so a systemd that rejects a property fails
# the probe rather than every command at spawn.
_SCOPE_CAP_PROPS: tuple[str, ...] = ("-p", "MemoryMax=2G", "-p", "TasksMax=512")

SyncConfirm = Callable[[ConfirmRequest], bool]

_SD_RUN_OK: bool | None = None  # lazily probed


def _systemd_run_available() -> bool:
    global _SD_RUN_OK
    if _SD_RUN_OK is None:
        try:
            probe = subprocess.run(
                ["systemd-run", "--user", "--scope", "-q", "--collect",
                 *_SCOPE_CAP_PROPS, "-p", "RuntimeMaxSec=10", "true"],
                capture_output=True, timeout=5,
            )
            _SD_RUN_OK = probe.returncode == 0
        except Exception:  # no systemd-run / no user manager -> plain subprocess
            _SD_RUN_OK = False
    return _SD_RUN_OK


def _truncate(s: str) -> str:
    return s[-_MAX_OUTPUT:]


class Executor:
    """Runs commands only after the denylist and the gate (plus confirmation) allow them."""

    def __init__(self, gate: ActionGate):
        self.gate = gate

    def _authorise(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        command: str | None,
        confirm: SyncConfirm | None,
    ) -> str | None:
        """Return an error string if denied/unconfirmed, else None (allowed)."""
        decision: Decision = self.gate.check(tool_name, args or {})
        if not decision.allowed:
            log.warning("executor: denied '%s' (%s)", tool_name, decision.reason)
            return f"denied: {decision.reason}"
        if decision.needs_confirm:
            request = ConfirmRequest(tool_name=tool_name, args=args or {}, command=command, detail="")
            ok = bool(confirm(request)) if confirm is not None else False
            if not ok:
                log.info("executor: '%s' not confirmed", tool_name)
                return "denied: action was not confirmed"
        return None

    def run_shell(
        self,
        command: str,
        *,
        tool_name: str = "shell.run_command",
        args: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        confirm: SyncConfirm | None = None,
        resource_caps: bool = True,
    ) -> dict:
        """Run a shell command through the denylist, the gate and the resource caps.

        Returns {returncode, stdout, stderr} or {error: ...} (with ``refused``/``denied`` set when
        the denylist or gate blocked it). ``resource_caps=False`` skips the scope, for detached GUI
        launches that must outlive it.
        """
        command = (command or "").strip()
        if not command:
            return {"error": "empty command"}
        reason = destructive_reason(command)
        if reason is not None:
            return {"error": f"refused: destructive command blocked by the safety denylist ({reason})",
                    "refused": reason}
        denied = self._authorise(tool_name, args, command=command, confirm=confirm)
        if denied is not None:
            return {"error": denied, "denied": True}
        if resource_caps and _systemd_run_available():
            argv = ["systemd-run", "--user", "--scope", "-q", "--collect",
                    *_SCOPE_CAP_PROPS, "-p", f"RuntimeMaxSec={int(timeout) + 2}", "/bin/sh", "-c", command]
            return self._spawn(argv, timeout, shell=False)
        return self._spawn(command, timeout, shell=True)

    def run_argv(
        self,
        argv: list[str],
        *,
        tool_name: str = "desktop.action",
        args: dict[str, Any] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        confirm: SyncConfirm | None = None,
        resource_caps: bool = True,
    ) -> dict:
        """Run a fixed argv list (shell=False) for a typed tool, through the same guards.

        The denylist still runs against the joined form, so even a typed tool cannot assemble a
        catastrophic command.
        """
        if not argv:
            return {"error": "empty argv"}
        joined = shlex.join(argv)
        reason = destructive_reason(joined)
        if reason is not None:
            return {"error": f"refused: destructive command blocked by the safety denylist ({reason})",
                    "refused": reason}
        denied = self._authorise(tool_name, args, command=joined, confirm=confirm)
        if denied is not None:
            return {"error": denied, "denied": True}
        if resource_caps and _systemd_run_available():
            wrapped = ["systemd-run", "--user", "--scope", "-q", "--collect",
                       *_SCOPE_CAP_PROPS, "-p", f"RuntimeMaxSec={int(timeout) + 2}", *argv]
            return self._spawn(wrapped, timeout, shell=False)
        return self._spawn(argv, timeout, shell=False)

    def launch_app(
        self,
        desktop_id: str,
        *,
        tool_name: str = "desktop.open_app_or_link",
        confirm: SyncConfirm | None = None,
    ) -> dict:
        """Launch an installed app by its .desktop id, so no PATH binary (poweroff, reboot) can run.

        Uses Gio.DesktopAppInfo (GLib, not GTK): it resolves only registered .desktop entries, which
        power-off/reboot/halt binaries do not have, enforcing the lock-only policy structurally.
        """
        app_id = (desktop_id or "").strip()
        if not app_id:
            return {"error": "launch_app needs a .desktop id"}
        if not app_id.endswith(".desktop"):
            app_id += ".desktop"
        denied = self._authorise(tool_name, {"desktop_id": app_id}, command=None, confirm=confirm)
        if denied is not None:
            return {"error": denied, "denied": True}
        try:
            from gi.repository import Gio  # GLib, GTK-free; imported lazily so denylist/gate stay pure
            info = Gio.DesktopAppInfo.new(app_id)
            if info is None:
                return {"error": f"no installed app with id '{app_id}'"}
            info.launch([], None)
        except Exception as exc:
            return {"error": f"failed to launch '{app_id}': {exc}"}
        return {"returncode": 0, "stdout": f"launched {app_id}", "stderr": ""}

    @staticmethod
    def _spawn(cmd, timeout: float, *, shell: bool) -> dict:
        try:
            proc = subprocess.run(
                cmd, shell=shell, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                timeout=timeout, cwd=_HOME,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"command timed out after {timeout}s"}
        except Exception as exc:
            return {"error": f"failed to run command: {exc}"}
        return {"returncode": proc.returncode, "stdout": _truncate(proc.stdout), "stderr": _truncate(proc.stderr)}
