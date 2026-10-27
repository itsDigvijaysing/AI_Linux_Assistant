"""Laya port: pure layout and calibration logic always; parity with the original when the export is installed."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import downloads, models_catalog  # noqa: E402
from ai_linux.engine import laya, small_models  # noqa: E402

GOLDEN = Path(__file__).parent / "fixtures" / "laya_golden.json"
HAVE_RUNTIME = all(importlib.util.find_spec(m) for m in ("tokenizers", "openvino"))


class FakeTok:
    """One id per word, so lengths are easy to reason about."""
    mask_token, cls_id, sep_id, mask_id = "[MASK]", 1, 2, 3

    def encode(self, text, max_length=None):
        ids = [100 + len(w) for w in text.split()]
        return ids[:max_length] if max_length is not None else ids


class Pure(unittest.TestCase):
    def test_clamp_temperature(self):
        self.assertEqual(laya.clamp_temperature(0.1006), 0.5)  # the shipped "choice:11+" value
        self.assertEqual(laya.clamp_temperature(9), 5.0)
        self.assertEqual(laya.clamp_temperature(1.76), 1.76)
        for bad in (True, float("nan"), float("inf"), "x", None):
            self.assertEqual(laya.clamp_temperature(bad), 1.0)

    def test_temp_bucket(self):
        self.assertEqual([laya.temp_bucket(k) for k in (2, 3, 5, 6, 10, 11, 26)],
                         ["choice:2", "choice:3-5", "choice:3-5", "choice:6-10", "choice:6-10", "choice:11+",
                          "choice:11+"])

    def test_render_option(self):
        self.assertEqual(laya.render_option("yes", ""), "yes")
        self.assertEqual(laya.render_option("yes", None), "yes")
        self.assertEqual(laya.render_option("vol", "change the volume"), "vol: change the volume")
        self.assertEqual(laya.render_option("x", {"a": 1}), 'x: {"a": 1}')

    def test_layout(self):
        ids, markers = laya.build_sequence(FakeTok(), "turn [MASK] up", "Which tool?", {"a": "one two", "b": ""},
                                           max_len=64, head_max_len=32)
        # [CLS] "choice" "question:" "Which" "tool?" [SEP] [MASK] a: one two [MASK] b [SEP] state [SEP]
        self.assertEqual(ids[0], 1)
        self.assertEqual(markers, [6, 10])
        self.assertTrue(all(ids[m] == 3 for m in markers))
        self.assertEqual(ids.count(3), 2)  # the [MASK] in the state was blanked out
        self.assertEqual(ids[-1], 2)

    def test_options_shrink_to_fit_the_head(self):
        opts = {f"o{i}": " ".join(["w"] * 30) for i in range(10)}
        ids, markers = laya.build_head(FakeTok(), "q", opts, head_max_len=96)
        per = max(4, (96 - 16) // 10)
        self.assertEqual(len(markers), 10)
        self.assertEqual(markers[1] - markers[0], per)

    def test_state_is_clamped_to_max_len(self):
        ids, markers = laya.build_sequence(FakeTok(), " ".join(["s"] * 500), "q", {"a": "", "b": ""},
                                           max_len=40, head_max_len=16)
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(markers), 2)


class Wiring(unittest.TestCase):
    def test_catalog(self):
        spec = models_catalog.get("laya")
        self.assertEqual((spec.slot, spec.runtime, spec.recommended), ("small", "openvino", False))
        self.assertEqual(models_catalog.local_path(spec), laya.LAYA_DIR)
        self.assertTrue(models_catalog.get("jiwo-0.8b").recommended)

    def test_downloads_table(self):
        self.assertEqual(set(downloads.OPENVINO_MODELS), {"jiwo-0.8b", "laya"})
        self.assertEqual(downloads.OPENVINO_MODELS["laya"].dir, laya.LAYA_DIR)
        self.assertTrue(downloads.openvino_published("jiwo-0.8b"))
        self.assertFalse(downloads.openvino_installed("unknown"))
        if not downloads.openvino_published("laya"):
            with self.assertRaises(downloads.DownloadError):
                downloads.openvino_download("laya")

    def test_load_missing(self):
        with mock.patch.object(laya, "installed", return_value=False):
            with self.assertRaises(FileNotFoundError):
                small_models.load("laya")


@unittest.skipUnless(HAVE_RUNTIME and laya.installed() and GOLDEN.is_file(), "Laya export or OpenVINO not here")
class Parity(unittest.TestCase):
    """Golden cases from Laya's own torch Agent (bench/router/laya_export.py)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(GOLDEN.read_text())
        cls.model = laya.Laya(device="CPU")

    def test_token_ids(self):
        m = self.model
        for c in self.cases:
            got = laya.build_sequence(m.tok, c["state"], c["question"], c["options"], m.max_len, m.head_max_len)
            self.assertEqual(got, (c["ids"], c["markers"]))

    def test_probabilities(self):
        for c in self.cases:
            p = self.model.ask(c["state"], c["question"], c["options"])
            ref = c["probs"]
            self.assertEqual(max(p, key=p.get), max(ref, key=ref.get), c["state"])
            for name in ref:
                self.assertAlmostEqual(p[name], ref[name], delta=0.03)


if __name__ == "__main__":
    unittest.main()
