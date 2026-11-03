"""scripts/install.sh and uninstall.sh in a throwaway home, with systemctl, sudo and apt stubbed out.

Only dry runs of the installer happen here: a real install downloads gigabytes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install.sh"
UNINSTALL = ROOT / "scripts/uninstall.sh"
ICON = "io.github.itsdigvijaysing.AILinux"
HAS_GI = importlib.util.find_spec("gi") is not None

# Every stub records its arguments; sudo and apt-get fail so a dry run that calls them is caught.
STUB = """#!/bin/sh
printf '%s\\n' "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0") $*" in
    "systemctl --user is-active"*) exit "${STUB_ACTIVE:-3}" ;;
    "systemctl --user show-environment") exit 1 ;;
    sudo* | apt-get*) exit 1 ;;
    pgrep*) exit 1 ;;
esac
exit 0
"""


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phoenix-installer-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        for name in ("systemctl", "sudo", "apt-get", "pgrep", "kill"):
            path = stubs / name
            path.write_text(STUB)
            path.chmod(0o755)
        self.log = self.tmp / "calls.log"
        self.env = {
            "HOME": str(self.home), "PATH": f"{stubs}:/usr/bin:/bin", "STUB_LOG": str(self.log),
            "LANG": "C.UTF-8", "PHOENIX_SYSTEM_PYTHON": sys.executable,
        }

    def sh(self, script: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(script), *args], env={**self.env, **env}, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def files(self) -> list[str]:
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))


class StaticChecks(unittest.TestCase):
    def test_syntax(self):
        for script in (INSTALL, UNINSTALL):
            subprocess.run(["bash", "-n", str(script)], check=True)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck isn't installed")
    def test_shellcheck_clean(self):
        res = subprocess.run(["shellcheck", str(INSTALL), str(UNINSTALL)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_no_em_dash(self):
        for script in (INSTALL, UNINSTALL):
            self.assertNotIn("\u2014", script.read_text())


class InstallDryRun(_Home):
    def test_changes_nothing(self):
        res = self.sh(INSTALL, "--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Dry run finished: nothing was changed.", res.stdout)
        self.assertEqual(self.files(), [])
        self.assertFalse([c for c in self.calls() if c.startswith(("sudo", "apt-get", "kill"))], self.calls())
        self.assertFalse([c for c in self.calls() if "ollama.service" in c and "ai-linux-ollama" not in c])

    def test_lists_the_steps(self):
        out = self.sh(INSTALL, "--dry-run").stdout
        for text in (".local/share/ai-linux/venv", ".local/bin/ai-linux-app", "ai-linux-ollama", "11435",
                     "Speech recognition", "Voices", "Models page"):
            self.assertIn(text, out)

    def test_skips_what_was_asked(self):
        out = self.sh(INSTALL, "--dry-run", "--no-ollama", "--no-models", "--skip-apt").stdout
        self.assertIn("Ollama: skipped", out)
        self.assertIn("Models: skipped", out)
        self.assertIn("System packages: skipped", out)
        self.assertNotIn("would download", out)

    def test_unknown_option(self):
        res = self.sh(INSTALL, "--bogus")
        self.assertEqual(res.returncode, 2)
        self.assertIn("Usage", res.stderr)

    @unittest.skipUnless(HAS_GI, "python3-gi isn't installed")
    def test_follows_chosen_models_without_touching_settings(self):
        config = self.home / ".config/ai-linux/app.json"
        config.parent.mkdir(parents=True)
        # An old-style file the app would migrate: the installer must read it, never rewrite it.
        text = json.dumps({"stt_engine": "parakeet-ctc", "small_model": "none", "power_mode": "battery"})
        config.write_text(text)
        out = self.sh(INSTALL, "--dry-run", "--skip-apt").stdout
        self.assertIn("Parakeet CTC 110M", out)
        self.assertNotIn("Parakeet TDT", out)
        self.assertNotIn("Small router model", out)
        self.assertEqual(config.read_text(), text)
        self.assertEqual(self.files(), [".config", ".config/ai-linux", ".config/ai-linux/app.json"])


class Uninstall(_Home):
    def setUp(self):
        super().setUp()
        h = self.home
        data, config = h / ".local/share/ai-linux", h / ".config/ai-linux"
        self.removed = [
            h / ".local/share/applications" / f"{ICON}.desktop",
            h / ".local/share/icons/hicolor/48x48/apps" / f"{ICON}.png",
            h / ".local/share/icons/hicolor/scalable/apps" / f"{ICON}.svg",
            h / ".local/share/icons/hicolor/symbolic/apps" / f"{ICON}-symbolic.svg",
            h / ".config/autostart" / f"{ICON}.desktop",
            data / "app/src/ai_linux/__init__.py", data / "venv/bin/python", data / "ollama/0.40.11/bin/ollama",
            h / ".cache/ai-linux/openvino/blob",
        ]
        self.kept = [
            config / "app.json", data / "history.db", data / "knowledge.json",
            data / "models/ASR/silero_vad_16k_op15.onnx", h / ".local/state/ai-linux/logs/engine.log",
            h / ".cache/supertonic3/onnx/x",
        ]
        self.untouched = [h / ".ollama/models/manifests/x", h / ".cache/huggingface/hub/x",
                          h / ".local/share/icons/hicolor/48x48/apps/other.png"]
        for path in self.removed + self.kept + self.untouched:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        self.launcher = h / ".local/bin/ai-linux-app"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.symlink_to(data / "app/bin/ai-linux-app")

    def test_dry_run_changes_nothing(self):
        before = self.files()
        res = self.sh(UNINSTALL, "--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.files(), before)
        self.assertIn("would remove", res.stdout)

    def test_keeps_settings_history_and_models(self):
        res = self.sh(UNINSTALL, "--yes")
        self.assertEqual(res.returncode, 0, res.stderr)
        for path in self.removed:
            self.assertFalse(path.exists(), path)
        self.assertFalse(self.launcher.is_symlink())
        for path in self.kept + self.untouched:
            self.assertTrue(path.exists(), path)

    def test_purge_deletes_everything_of_ours_only(self):
        res = self.sh(UNINSTALL, "--yes", "--purge")
        self.assertEqual(res.returncode, 0, res.stderr)
        for path in self.removed + self.kept:
            self.assertFalse(path.exists(), path)
        for name in (".config/ai-linux", ".local/share/ai-linux", ".local/state/ai-linux", ".cache/ai-linux"):
            self.assertFalse((self.home / name).exists(), name)
        for path in self.untouched:
            self.assertTrue(path.exists(), path)

    def test_asks_without_a_terminal_and_yes(self):
        res = self.sh(UNINSTALL)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("--yes", res.stderr)
        self.assertTrue(all(p.exists() for p in self.removed))

    def test_leaves_a_foreign_launcher(self):
        self.launcher.unlink()
        self.launcher.write_text("#!/bin/sh\n")
        self.sh(UNINSTALL, "--yes")
        self.assertTrue(self.launcher.is_file())

    def test_stops_only_our_ollama_unit(self):
        self.sh(UNINSTALL, "--yes", STUB_ACTIVE="0")
        stops = [c for c in self.calls() if c.startswith("systemctl") and " stop " in f"{c} "]
        self.assertEqual(stops, ["systemctl --user stop ai-linux-ollama.service"])
        self.assertFalse([c for c in self.calls() if c.startswith(("sudo", "apt-get"))])

    def test_idle_ollama_is_left_alone(self):
        self.sh(UNINSTALL, "--yes")
        self.assertFalse([c for c in self.calls() if " stop " in f"{c} "])

    def test_nothing_installed(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        res = self.sh(UNINSTALL, "--yes", HOME=str(empty))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Nothing of Phoenix is installed", res.stdout)


if __name__ == "__main__":
    unittest.main()
