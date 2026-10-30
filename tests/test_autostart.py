"""Autostart entry on a throwaway XDG_CONFIG_HOME: /usr/bin/python3 -m unittest discover -s tests."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import autostart  # noqa: E402
from ai_linux.paths import ICON_NAME, REPO_ROOT  # noqa: E402


class AutostartTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "config"
        self.system = root / "xdg"
        self._env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home),
                                                 "XDG_CONFIG_DIRS": str(self.system)})
        self._env.start()
        self.path = self.home / "autostart" / f"{ICON_NAME}.desktop"

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _values(self) -> dict[str, str]:
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        return dict(line.split("=", 1) for line in lines[1:] if "=" in line)

    def test_off_by_default(self):
        self.assertFalse(autostart.is_enabled())
        self.assertEqual(autostart.desktop_path(), self.path)

    def test_enable_writes_entry(self):
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())
        v = self._values()
        self.assertEqual(v["Name"], "Phoenix")
        self.assertEqual(v["Icon"], ICON_NAME)
        self.assertEqual(v["X-GNOME-Autostart-enabled"], "true")
        self.assertEqual(v["NoDisplay"], "false")
        self.assertEqual(v["Type"], "Application")
        self.assertTrue(v["Exec"].endswith(" --background"))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)
        # atomic write leaves no temp files behind
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_enable_is_idempotent(self):
        autostart.set_enabled(True)
        first = self.path.read_text()
        autostart.set_enabled(True)
        self.assertEqual(self.path.read_text(), first)

    def test_disable_removes_entry(self):
        autostart.set_enabled(True)
        autostart.set_enabled(False)
        self.assertFalse(self.path.exists())
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(False)  # removing twice is fine

    def test_launcher_falls_back_to_repo(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(autostart.launcher(), str(REPO_ROOT / "bin" / "ai-linux-app"))
        with mock.patch("shutil.which", return_value="/usr/bin/ai-linux-app"):
            autostart.set_enabled(True)
        self.assertEqual(self._values()["Exec"], "/usr/bin/ai-linux-app --background")

    def test_exec_quotes_spaces(self):
        with mock.patch("shutil.which", return_value="/opt/my apps/ai-linux-app"):
            autostart.set_enabled(True)
        self.assertEqual(self._values()["Exec"], '"/opt/my apps/ai-linux-app" --background')

    def test_quote_rules(self):
        self.assertEqual(autostart._quote("/usr/bin/x"), "/usr/bin/x")
        self.assertEqual(autostart._quote("/a/50%"), "/a/50%%")
        self.assertEqual(autostart._quote('/a b/"q"'), '"/a b/\\\\"q\\\\""')

    def test_disabled_flag_counts_as_off(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[Desktop Entry]\nType=Application\nX-GNOME-Autostart-enabled=false\n")
        self.assertFalse(autostart.is_enabled())

    def test_system_entry_is_overridden_when_disabled(self):
        sys_entry = self.system / "autostart" / self.path.name
        sys_entry.parent.mkdir(parents=True)
        sys_entry.write_text("[Desktop Entry]\nType=Application\nExec=x\n")
        self.assertTrue(autostart.is_enabled())
        autostart.set_enabled(False)
        self.assertEqual(self._values()["Hidden"], "true")
        self.assertFalse(autostart.is_enabled())
        autostart.set_enabled(True)
        self.assertTrue(autostart.is_enabled())


if __name__ == "__main__":
    unittest.main()
