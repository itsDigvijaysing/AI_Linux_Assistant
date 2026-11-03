"""pyproject.toml describes v3 (ai-linux) and its engine pins match requirements-engine.txt."""

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pins(lines: list[str]) -> list[str]:
    # A requirement without its comment or spacing, so both files compare by content.
    out = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(re.sub(r"\s+", "", line))
    return out


class PyprojectTests(unittest.TestCase):
    def setUp(self):
        self.data = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.project = self.data["project"]

    def test_names_v3(self):
        self.assertEqual(self.project["name"], "ai-linux")
        self.assertIn("Phoenix", self.project["description"])
        self.assertNotIn("glados", (ROOT / "pyproject.toml").read_text().lower())

    def test_version_comes_from_the_version_file(self):
        self.assertIn("version", self.project.get("dynamic", []))
        self.assertNotIn("version", self.project)
        self.assertEqual(self.data["tool"]["hatch"]["version"]["path"], "VERSION")

    def test_needs_python_3_14(self):
        # ollama.py unpacks Ollama's .tar.zst with tarfile mode "r|zst", new in 3.14.
        self.assertEqual(self.project["requires-python"], ">=3.14")

    def test_no_entry_point(self):
        # The app starts from bin/ai-linux-app; v2's "glados" command is gone.
        self.assertNotIn("scripts", self.project)

    def test_app_and_engine_groups(self):
        groups = self.project["optional-dependencies"]
        self.assertEqual(set(groups), {"app", "engine"})
        self.assertTrue(any(d.startswith("PyGObject") for d in groups["app"]))

    def test_engine_group_matches_requirements_file(self):
        want = _pins((ROOT / "requirements-engine.txt").read_text().splitlines())
        self.assertEqual(_pins(self.project["optional-dependencies"]["engine"]), want)


if __name__ == "__main__":
    unittest.main()
