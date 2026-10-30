"""A yes/no question opens on its own, not with the whole Phoenix window (no display needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from ai_linux.app import App
except Exception:  # noqa: BLE001  needs python3-gi
    App = None


def window(visible: bool, suspended: bool = False):
    calls = []
    return SimpleNamespace(get_visible=lambda: visible, is_suspended=lambda: suspended,
                           present=lambda: calls.append("present"), calls=calls)


@unittest.skipIf(App is None, "needs python3-gi")
class ConfirmParentTests(unittest.TestCase):
    def test_hidden_or_minimised_window_is_left_alone(self):
        self.assertIsNone(App._dialog_parent(None))  # never opened
        self.assertIsNone(App._dialog_parent(window(False)))  # closed to the tray
        self.assertIsNone(App._dialog_parent(window(True, suspended=True)))  # minimised or another workspace

    def test_open_window_holds_the_question(self):
        win = window(True)
        self.assertIs(App._dialog_parent(win), win)
        old_gtk = SimpleNamespace(get_visible=lambda: True)  # GTK before 4.12 has no is_suspended
        self.assertIs(App._dialog_parent(old_gtk), old_gtk)


if __name__ == "__main__":
    unittest.main()
