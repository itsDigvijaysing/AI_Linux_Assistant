"""The open-source credits list and the refs/ sync script that reads it."""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_linux import credits  # noqa: E402
from ai_linux.credits import CREDITS, Credit  # noqa: E402

_spec = importlib.util.spec_from_file_location("sync_refs", ROOT / "scripts" / "sync_refs.py")
sync_refs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_refs)


class CreditsTests(unittest.TestCase):
    def test_every_entry_is_complete(self):
        kinds = {k for k, _t, _d in credits.KINDS}
        for c in CREDITS:
            self.assertIn(c.kind, kinds, c.name)
            self.assertTrue(c.name and c.role and c.licence, c.name)
            self.assertTrue(c.url.startswith("https://"), c.name)
            self.assertFalse(c.git and not c.git.startswith("https://"), c.name)
        self.assertEqual(len({c.name for c in CREDITS}), len(CREDITS))

    def test_adapted_code_ships_its_licence_text(self):
        for c in credits.by_kind("code"):
            self.assertTrue(c.licence_file and (ROOT / c.licence_file).is_file(), c.name)
            self.assertTrue(c.owner, c.name)

    def test_a_few_names_are_featured(self):
        self.assertIn(len(credits.featured()), (4, 5))

    def test_adapted_code_has_a_standard_licence(self):
        for c in credits.by_kind("code"):
            self.assertIsNotNone(credits.gtk_licence(c), c.name)

    def test_full_page_names_everyone_and_thanks(self):
        text = credits.notices_text()
        self.assertIn("Thank you to the open-source community", text)
        for c in CREDITS:
            self.assertIn(f"{c.name} ({c.licence}", text)

    def test_licence_files_exist(self):
        for c in CREDITS:
            if c.licence_file:
                self.assertTrue((ROOT / c.licence_file).is_file(), c.licence_file)

    def test_refs_folders_keep_their_names(self):
        dirs = [credits.ref_dir(c) for c in CREDITS if c.git]
        self.assertEqual(len(dirs), len(set(dirs)))
        for name in ("GLaDOS", "computer-use-linux", "Newelle", "RealtimeVoiceChat", "fabric", "aichat"):
            self.assertIn(name, dirs)

    def test_gtk_licence_names_exist(self):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk
        except (ImportError, ValueError):
            self.skipTest("GTK 4 not available")
        for c in CREDITS:
            name = credits.gtk_licence(c)
            if name:
                self.assertTrue(hasattr(Gtk.License, name), name)
        self.assertIsNone(credits.gtk_licence(Credit("x", "y", "CC-BY-4.0", "https://x", "model")))


class SyncRefsTests(unittest.TestCase):
    def setUp(self):
        self.refs = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.refs)
        self.credit = Credit("demo", "test", "MIT", "https://example.com", "reference",
                             git="https://example.com/demo.git")

    def make_clone(self, dirty=False):
        dest = self.refs / "demo"
        dest.mkdir()
        run = lambda *a: subprocess.run(["git", *a], cwd=dest, capture_output=True, check=True)  # noqa: E731
        run("init", "-q")
        (dest / "a.txt").write_text("a")
        run("add", "a.txt")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
        if dirty:
            (dest / "a.txt").write_text("changed")

    def test_missing_folder_is_cloned(self):
        self.assertEqual(sync_refs.action(self.refs / "demo"), "clone")
        self.assertEqual(sync_refs.sync(self.credit, self.refs, dry=True), "would clone")

    def test_clean_clone_is_updated(self):
        self.make_clone()
        self.assertEqual(sync_refs.action(self.refs / "demo"), "update")

    def test_local_edits_are_never_reset(self):
        self.make_clone(dirty=True)
        self.assertEqual(sync_refs.sync(self.credit, self.refs), "skipped: local edits")
        self.assertEqual((self.refs / "demo" / "a.txt").read_text(), "changed")


if __name__ == "__main__":
    unittest.main()
