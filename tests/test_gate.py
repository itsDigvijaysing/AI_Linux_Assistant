"""Tests for the action gate (ai_linux.safety.gate). Run:
    PYTHONPATH=src python3 -m unittest tests.test_gate -v
"""

from __future__ import annotations

import unittest

from ai_linux.safety.gate import ActionGate, ConfirmRequest, Decision, GATED_FAMILIES, sync_confirm

GATED = ["shell.run_command", "desktop.set_volume", "desktop.open_app_or_link", "computer_use.click"]
UNGATED = ["system_info.battery", "time_info.now", "voice.set_voice", "system_info.network_status"]


class TestGating(unittest.TestCase):
    def test_ungated_always_allowed_no_confirm(self):
        for armed in (True, False):
            for auto in (True, False):
                gate = ActionGate(armed=armed, autonomy=auto)
                for tool in UNGATED:
                    d = gate.check(tool)
                    self.assertTrue(d.allowed)
                    self.assertFalse(d.needs_confirm)

    def test_is_gated(self):
        gate = ActionGate(armed=True)
        for tool in GATED:
            self.assertTrue(gate.is_gated(tool), tool)
        for tool in UNGATED:
            self.assertFalse(gate.is_gated(tool), tool)


class TestArming(unittest.TestCase):
    def test_disarmed_denies_gated(self):
        gate = ActionGate(armed=False)
        for tool in GATED:
            d = gate.check(tool)
            self.assertFalse(d.allowed, tool)

    def test_armed_allows_gated_with_confirm_by_default(self):
        gate = ActionGate(armed=True)  # default confirm_globs = all gated families
        for tool in GATED:
            d = gate.check(tool)
            self.assertTrue(d.allowed, tool)
            self.assertTrue(d.needs_confirm, tool)

    def test_empty_confirm_globs_arms_without_confirm(self):
        gate = ActionGate(armed=True, confirm_globs=[])
        for tool in GATED:
            d = gate.check(tool)
            self.assertTrue(d.allowed)
            self.assertFalse(d.needs_confirm)

    def test_confirm_only_for_shell(self):
        gate = ActionGate(armed=True, confirm_globs=["shell.run_command"])
        self.assertTrue(gate.check("shell.run_command").needs_confirm)
        self.assertFalse(gate.check("desktop.set_volume").needs_confirm)
        self.assertTrue(gate.check("desktop.set_volume").allowed)


class TestAutonomyFloor(unittest.TestCase):
    def test_autonomy_denies_gated_regardless(self):
        # even armed with no confirm globs, autonomy denies every gated family
        gate = ActionGate(armed=True, confirm_globs=[], autonomy=True)
        for tool in GATED:
            d = gate.check(tool)
            self.assertFalse(d.allowed, tool)

    def test_autonomy_matches_gated_families(self):
        self.assertEqual(set(GATED_FAMILIES), {"shell.run_command", "desktop.*", "computer_use.*", "mcp.*"})


class TestSyncConfirm(unittest.TestCase):
    def _req(self):
        return ConfirmRequest(tool_name="shell.run_command", args={}, command="echo hi")

    def test_no_hook_denies(self):
        self.assertFalse(sync_confirm(None, self._req()))

    def test_hook_yes(self):
        self.assertTrue(sync_confirm(lambda req, cb: cb(True), self._req()))

    def test_hook_no(self):
        self.assertFalse(sync_confirm(lambda req, cb: cb(False), self._req()))

    def test_hook_raises_denies(self):
        def boom(req, cb):
            raise RuntimeError("ui gone")
        self.assertFalse(sync_confirm(boom, self._req()))

    def test_hook_timeout_denies(self):
        self.assertFalse(sync_confirm(lambda req, cb: None, self._req(), timeout=0.2))


class TestExecutorStdin(unittest.TestCase):
    def test_command_cannot_read_callers_stdin(self):
        import os
        from ai_linux.safety.executor import Executor
        r, w = os.pipe()  # a parent stdin that never closes: a command reading it would hang
        saved = os.dup(0)
        os.dup2(r, 0)
        try:
            ex = Executor(ActionGate(armed=True, confirm_globs=[]))
            out = ex.run_shell("cat; echo done", resource_caps=False, timeout=5)
        finally:
            os.dup2(saved, 0)
            for fd in (saved, r, w):
                os.close(fd)
        self.assertEqual(out.get("stdout"), "done\n")


class TestDecision(unittest.TestCase):
    def test_decision_defaults(self):
        d = Decision(allowed=True)
        self.assertFalse(d.needs_confirm)
        self.assertEqual(d.reason, "")


if __name__ == "__main__":
    unittest.main()
