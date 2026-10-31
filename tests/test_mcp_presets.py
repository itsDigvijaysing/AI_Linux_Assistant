"""MCP presets, per-tool switches and the engine's tool list reaching the Tools page (no servers started)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import mcp_presets  # noqa: E402


class PresetTests(unittest.TestCase):
    def test_presets_fill_a_valid_server_with_home_written_out(self):
        files = mcp_presets.get("files").server(Path("/home/me"))
        self.assertEqual((files["command"], files["args"]),
                         ("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/Documents"]))
        self.assertEqual(mcp_presets.get("git").server(Path("/home/me"))["args"],
                         ["mcp-server-git", "--repository", "/home/me"])
        for p in mcp_presets.PRESETS:
            s = p.server(Path("/home/me"))
            self.assertTrue(s["confirm"] and s["enabled"])  # asks before each use, like a hand-added server
            self.assertFalse(any("~" in a for a in s["args"]))
            self.assertRegex(s["name"], r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
            self.assertNotIn("\u2014", p.title + p.blurb)

    def test_only_read_only_tools_start_on(self):
        self.assertNotIn("read_text_file", mcp_presets.get("files").off)
        self.assertIn("write_file", mcp_presets.get("files").off)
        self.assertIn("git_commit", mcp_presets.get("git").off)
        self.assertNotIn("git_status", mcp_presets.get("git").off)

    def test_missing_program_is_reported(self):
        with mock.patch.object(mcp_presets.shutil, "which", return_value=None):
            self.assertFalse(mcp_presets.get("files").available())


class ToolSwitchTests(unittest.TestCase):
    def page(self, servers):
        from ai_linux.ui.pages.tools import Page
        saved = []
        fake = SimpleNamespace(_servers=lambda: [dict(s) for s in servers],
                               _find=lambda lst, name: Page._find(None, lst, name),
                               _save_servers=lambda lst: saved.append(lst) or True)
        return fake, saved, Page._set_tool

    def test_turning_tools_off_and_on(self):
        fake, saved, set_tool = self.page([{"name": "files", "disabled_tools": ["write_file"]}])
        set_tool(fake, "files", "move_file", False)
        self.assertEqual(saved[-1][0]["disabled_tools"], ["move_file", "write_file"])
        set_tool(fake, "files", "write_file", True)
        self.assertEqual(saved[-1][0]["disabled_tools"], [])
        n = len(saved)
        set_tool(fake, "files", "read_text_file", True)  # already on: nothing to save
        set_tool(fake, "nope", "x", False)
        self.assertEqual(len(saved), n)


class EngineToolListTests(unittest.TestCase):
    def test_the_engines_tool_list_reaches_the_bus(self):
        from ai_linux.engine_client import EngineClient
        from ai_linux.state import Bus
        bus = Bus()
        seen = []
        bus.connect("mcp-tools-changed", lambda *_: seen.append(dict(bus.mcp_tools)))
        client = EngineClient.__new__(EngineClient)
        client.bus = bus
        client._event({"ev": "mcp_tools", "servers": {"files": [{"name": "list_directory", "description": "x"},
                                                                {"bad": 1}]}})
        self.assertEqual(seen, [{"files": [{"name": "list_directory", "description": "x"}]}])
        client._event({"ev": "mcp_tools", "servers": "garbage"})
        self.assertEqual(bus.mcp_tools, {})


if __name__ == "__main__":
    unittest.main()
