"""Health checks without touching real hardware: /usr/bin/python3 -m unittest discover -s tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import doctor  # noqa: E402
from ai_linux.doctor import Check  # noqa: E402


class _Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key):
        return self.values.get(key, doctor.DEFAULTS[key])

    def is_set(self, key):
        return key in self.values


def _probe(**settings) -> doctor._Probe:
    return doctor._Probe(SimpleNamespace(settings=_Settings(**settings), ollama=None, controller=None))


def _fake_pci(root: Path, slot: str, cls: str, vendor: str, device: str, driver: str | None, runtime: str):
    dev = root / "bus/pci/devices" / slot
    (dev / "power").mkdir(parents=True)
    (dev / "class").write_text(cls + "\n")
    (dev / "vendor").write_text(vendor + "\n")
    (dev / "device").write_text(device + "\n")
    (dev / "power/runtime_status").write_text(runtime + "\n")
    if driver:
        target = root / "bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        os.symlink(target, dev / "driver")


class RunnerTest(unittest.TestCase):
    def test_every_check_returns_valid_rows(self):
        with mock.patch.object(doctor, "_http_json", side_effect=OSError("refused")):
            checks = doctor.run_checks(None, timeout=10)
        self.assertTrue(checks)
        ids = [c.id for c in checks]
        for expected in ("pipewire", "microphone", "aec", "ollama", "model_small", "model_big", "tools",
                         "shortcuts", "tray", "pill", "autostart", "python", "toolkit"):
            self.assertIn(expected, ids)
        group_ids = {g for g, _ in doctor.GROUPS}
        for c in checks:
            self.assertIn(c.status, doctor.STATUSES, c)
            self.assertIn(c.group, group_ids, c)
            self.assertTrue(c.fix_id is None or c.fix_id in doctor.FIXES, c)

    def test_a_broken_check_never_stops_the_rest(self):
        def boom(_probe):
            raise RuntimeError("kaput")

        spec = doctor._Spec("boom", "Boom", "system", boom)
        with mock.patch.object(doctor, "CHECKS", [spec, *doctor.CHECKS]):
            checks = doctor.run_checks(None, timeout=10, only=["boom", "python"])
        by_id = {c.id: c for c in checks}
        self.assertEqual(by_id["boom"].status, "warn")
        self.assertIn("kaput", by_id["boom"].detail)
        self.assertEqual(by_id["python"].status, "info")

    def test_a_slow_check_times_out(self):
        def slow(_probe):
            time.sleep(3)
            return Check("slow", "Slow", "ok")

        spec = doctor._Spec("slow", "Slow", "system", slow)
        with mock.patch.object(doctor, "CHECKS", [spec]):
            t0 = time.monotonic()
            checks = doctor.run_checks(None, timeout=0.3)
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual((checks[0].id, checks[0].status, checks[0].detail), ("slow", "warn", "Timed out"))

    def test_summarize(self):
        self.assertEqual(doctor.summarize([Check("a", "A", "ok"), Check("b", "B", "info")]), ("ok", 0))
        self.assertEqual(doctor.summarize([Check("a", "A", "warn"), Check("b", "B", "fail")]), ("fail", 2))
        self.assertEqual(doctor.summarize([]), ("ok", 0))


class ReportTest(unittest.TestCase):
    def test_report_scrubs_home_and_groups_rows(self):
        home = str(Path.home())
        text = doctor.report_text([
            Check("logs", "Logs", "info", f"{home}/.local/state/ai-linux/logs", group="system"),
            Check("ollama", "Ollama", "warn", "Installed but not running", "start_ollama", group="models"),
        ])
        self.assertNotIn(home, text)
        self.assertIn("~/.local/state/ai-linux/logs", text)
        self.assertIn("[WARN] Ollama: Installed but not running", text)
        self.assertLess(text.index("Models"), text.index("About this system"))
        self.assertIn("1 item(s) need attention", text)
        self.assertIn(doctor.__version__, text)


class OllamaTest(unittest.TestCase):
    def test_normalize_status_shapes(self):
        class St(Enum):
            RUNNING = "running"
            NOT_INSTALLED = "not_installed"

        n = doctor._normalize_ollama_status
        self.assertEqual(n("Running"), "running")
        self.assertEqual(n("not-running"), "stopped")
        self.assertEqual(n(St.RUNNING), "running")
        self.assertEqual(n(St.NOT_INSTALLED), "missing")
        self.assertEqual(n({"state": "starting"}), "starting")
        self.assertEqual(n(SimpleNamespace(running=True)), "running")
        self.assertIsNone(n("weird"))
        self.assertIsNone(n(None))

    def test_running_server_lists_models(self):
        def fake(url, timeout=1.0):
            if url.endswith("/api/version"):
                return {"version": "0.30.8"}
            return {"models": [{"name": "qwen3.5:0.8b", "model": "qwen3.5:0.8b"}]}

        p = _probe()
        with mock.patch.object(doctor, "_http_json", side_effect=fake):
            self.assertEqual(doctor.check_ollama(p).status, "ok")
            small, big = doctor.check_model_small(p), doctor.check_model_big(p)
        self.assertEqual(small.status, "ok")
        self.assertEqual((big.status, big.fix_id), ("warn", "open_models"))
        self.assertIn("3.3 GB", big.detail)

    def test_stopped_server_offers_start(self):
        mgr = SimpleNamespace(status="stopped", start=lambda: None, binary=None)
        p = doctor._Probe(SimpleNamespace(settings=_Settings(), ollama=mgr))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"OLLAMA_MODELS": tmp}), \
                mock.patch.object(doctor, "_http_json", side_effect=OSError("refused")), \
                mock.patch.object(doctor, "_ollama_binary", return_value="/usr/bin/ollama"), \
                mock.patch.object(Path, "home", return_value=Path(tmp)):
            c = doctor.check_ollama(p)
            self.assertEqual((c.status, c.fix_id), ("warn", "start_ollama"))
            self.assertEqual(doctor.check_model_big(p).fix_id, "open_models")
            # read from Ollama's store on disk while the server is stopped
            manifest = Path(tmp) / "manifests/registry.ollama.ai/library/qwen3.5/4b"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}")
            self.assertEqual(doctor.check_model_big(p).status, "ok")

    def test_missing_ollama_fails(self):
        with mock.patch.object(doctor, "_http_json", side_effect=OSError("refused")), \
                mock.patch.object(doctor, "_ollama_binary", return_value=None):
            c = doctor.check_ollama(_probe())
        self.assertEqual(c.status, "fail")

    def test_tag_matching_ignores_latest(self):
        self.assertTrue(doctor._tag_present({"gemma4:e4b"}, "gemma4:e4b"))
        self.assertTrue(doctor._tag_present({"foo:latest"}, "foo"))
        self.assertFalse(doctor._tag_present({"qwen3.5:4b"}, "qwen3.5:0.8b"))

    def test_encoder_model_found_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(doctor, "MODELS_DIR", Path(tmp)):
            p = _probe(small_model="jiwo-0.8b")
            self.assertEqual(doctor.check_model_small(p).status, "warn")
            (Path(tmp) / "jiwo-0.8b-openvino-int8").mkdir()
            (Path(tmp) / "jiwo-0.8b-openvino-int8" / "openvino_model.bin").write_bytes(b"x")
            self.assertEqual(doctor.check_model_small(_probe(small_model="jiwo-0.8b")).status, "warn")  # partial
            from ai_linux import downloads
            with mock.patch.object(downloads, "jiwo_installed", return_value=True) as done:
                self.assertEqual(doctor.check_model_small(_probe(small_model="jiwo-0.8b")).status, "ok")
            done.assert_called_with(Path(tmp) / "jiwo-0.8b-openvino-int8")


class GpuTest(unittest.TestCase):
    def test_reads_sysfs_without_waking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fake_pci(root, "0000:00:02.0", "0x030000", "0x8086", "0x46a6", "i915", "active")
            _fake_pci(root, "0000:01:00.0", "0x030000", "0x10de", "0x2520", "nvidia", "suspended")
            _fake_pci(root, "0000:00:1f.3", "0x040380", "0x8086", "0x51c8", "snd_hda_intel", "active")
            gpus = doctor.list_gpus(root)
            self.assertEqual([g["brand"] for g in gpus], ["Intel", "NVIDIA"])
            checks = doctor.check_gpus(_probe(), sys_root=root)
        nvidia = checks[1]
        self.assertEqual((nvidia.status, nvidia.title), ("ok", "NVIDIA graphics"))
        self.assertIn("asleep to save power", nvidia.detail)
        self.assertEqual(checks[0].status, "info")

    def test_nvidia_without_driver_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fake_pci(root, "0000:01:00.0", "0x030200", "0x10de", "0x2520", None, "active")
            checks = doctor.check_gpus(_probe(), sys_root=root)
        self.assertEqual(checks[0].status, "warn")
        self.assertIn("no driver loaded", checks[0].detail)

    def test_no_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = doctor.check_gpus(_probe(), sys_root=Path(tmp))
        self.assertEqual(checks[0].status, "info")


class DesktopTest(unittest.TestCase):
    def test_missing_tools_name_packages(self):
        with mock.patch.object(doctor, "_which", side_effect=lambda b: None if b == "wl-copy" else "/usr/bin/" + b):
            c = doctor.check_tools(_probe())
        self.assertEqual(c.status, "warn")
        self.assertIn("wl-clipboard", c.detail)

    def test_pill_needs_display(self):
        with mock.patch.dict(os.environ, {"DISPLAY": ""}):
            self.assertEqual(doctor.check_pill(_probe()).status, "warn")
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
            self.assertEqual(doctor.check_pill(_probe()).status, "ok")

    def test_pill_prefers_running_indicator(self):
        def probe(pill):
            return doctor._Probe(SimpleNamespace(settings=_Settings(), ollama=None, controller=None, pill=pill))

        with mock.patch.dict(os.environ, {"DISPLAY": ""}):
            c = doctor.check_pill(probe(SimpleNamespace(available=True, backend="layer-shell")))
            self.assertEqual(c.status, "ok")
            self.assertIn("layer-shell", c.detail)
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
            self.assertEqual(doctor.check_pill(probe(SimpleNamespace(available=False, backend=None))).status, "warn")

    def test_autostart_fix_offered(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            c = doctor.check_autostart(_probe(autostart=True))
            self.assertEqual((c.status, c.fix_id), ("warn", "enable_autostart"))
            c = doctor.check_autostart(_probe(autostart=False))
            self.assertEqual((c.status, c.fix_id), ("info", "enable_autostart"))
            c = doctor.check_autostart(_probe())  # the default alone is not the user's choice
            self.assertEqual((c.status, c.fix_id), ("info", "enable_autostart"))

    def test_microphone_parsing(self):
        out = ('\tid 44, type PipeWire:Interface:Node/3\n \t\tnode.description = "USB Mic"\n'
               ' \t\tmedia.class = "Audio/Source"\n\tid 45, type PipeWire:Interface:Node/3\n'
               ' \t\tnode.description = "Speakers"\n \t\tmedia.class = "Audio/Sink"\n')
        with mock.patch.object(doctor, "_run", return_value=(0, out)):
            c = doctor.check_microphone(_probe())
        self.assertEqual((c.status, c.detail), ("ok", "USB Mic"))
        with mock.patch.object(doctor, "_run", return_value=None):
            self.assertEqual(doctor.check_microphone(_probe()).status, "warn")
            self.assertEqual(doctor.check_pipewire(_probe()).status, "fail")


if __name__ == "__main__":
    unittest.main()
