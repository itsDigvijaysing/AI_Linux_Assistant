"""App-grid launcher install and uninstall in a throwaway XDG data home."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import desktop_entry  # noqa: E402
from ai_linux.paths import ICON_NAME  # noqa: E402


class DesktopEntry(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_install_writes_entry_and_icons(self):
        written = desktop_entry.install()
        self.assertEqual(written, desktop_entry.installed_paths())
        self.assertTrue(all(p.is_file() and p.is_relative_to(self.home) for p in written))
        text = desktop_entry.entry_path().read_text()
        self.assertIn(f"Icon={ICON_NAME}\n", text)
        self.assertIn("Exec=", text)
        self.assertNotIn("--background", text)
        self.assertEqual(desktop_entry.entry_path().name, f"{ICON_NAME}.desktop")

    def test_entry_is_valid(self):
        validator = shutil.which("desktop-file-validate")
        if not validator:
            self.skipTest("desktop-file-validate not installed")
        desktop_entry.install()
        result = subprocess.run([validator, str(desktop_entry.entry_path())], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_uninstall_removes_only_its_files(self):
        desktop_entry.install()
        other = self.home / "applications" / "other.desktop"
        other.write_text("[Desktop Entry]\n")
        removed = desktop_entry.uninstall()
        self.assertEqual(len(removed), len(desktop_entry.ICONS) + 1)
        self.assertFalse(any(p.exists() for p in desktop_entry.installed_paths()))
        self.assertTrue(other.exists())
        self.assertEqual(desktop_entry.uninstall(), [])

    def test_cli_rejects_unknown_action(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(desktop_entry.main([]), 2)
            self.assertEqual(desktop_entry.main(["bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
