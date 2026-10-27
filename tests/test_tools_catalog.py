"""The Tools page catalog must match the engine's real tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import tools_catalog  # noqa: E402
from ai_linux.engine import tools as engine_tools  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_same_tools_as_engine(self):
        real = {t.name for t in engine_tools.TOOLS}
        catalog = {t.name for t in tools_catalog.TOOLS}
        self.assertEqual(catalog, real, f"missing: {sorted(real - catalog)} extra: {sorted(catalog - real)}")

    def test_gating_matches_engine(self):
        gated = {t.name for t in engine_tools.TOOLS if t.family in ("desktop", "shell")}
        self.assertEqual({t.name for t in tools_catalog.TOOLS if t.gated}, gated)

    def test_names_unique_and_fields_filled(self):
        names = [t.name for t in tools_catalog.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for t in tools_catalog.TOOLS:
            self.assertTrue(t.title and t.description and t.family in tools_catalog.FAMILIES)
            self.assertNotIn("—", t.title + t.description)

    def test_lookup_accepts_qualified_names(self):
        self.assertEqual(tools_catalog.lookup("mcp.skills_actions.set_volume").name, "set_volume")
        self.assertEqual(tools_catalog.lookup("mcp.shell.run_command").name, "run_command")
        self.assertEqual(tools_catalog.lookup("go_to_sleep").qualified, "go_to_sleep")
        self.assertIsNone(tools_catalog.lookup("nope"))
        self.assertEqual(tools_catalog.title_for("mcp.weather.forecast"), "mcp.weather.forecast")


if __name__ == "__main__":
    unittest.main()
