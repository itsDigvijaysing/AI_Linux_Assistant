"""Safety core: catastrophic-command denylist, action gate, and the gated executor."""

from __future__ import annotations

from ai_linux.safety.denylist import destructive_reason
from ai_linux.safety.executor import Executor
from ai_linux.safety.gate import ActionGate, ConfirmHook, ConfirmRequest, Decision, sync_confirm

__all__ = [
    "destructive_reason",
    "Executor",
    "ActionGate",
    "Decision",
    "ConfirmRequest",
    "ConfirmHook",
    "sync_confirm",
]
