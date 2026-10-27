"""OllamaManager against a fake local Ollama (chunked NDJSON pulls, logprobs); never the real network."""

from __future__ import annotations

import hashlib
import io
import json
import math
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gi.repository import GLib  # noqa: E402

from ai_linux import ollama  # noqa: E402
from ai_linux.config import Settings  # noqa: E402

DIGEST_BIG = "sha256:" + "6a" * 32
DIGEST_SMALL = "sha256:" + "b1" * 32


def pump(cond, timeout=10.0):
    """Iterate the default main context until cond() is true (callbacks arrive via idle_add)."""
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FakeOllama:
    def __init__(self):
        self.models = [
            {"name": "qwen3.5:4b", "model": "qwen3.5:4b", "size": 3324173757,
             "modified_at": "2026-10-20T10:00:00+05:30", "details": {"parameter_size": "4.7B"}},
            {"name": "qwen3:4b-instruct", "model": "qwen3:4b-instruct", "size": 2497293316,
             "modified_at": "2026-07-01T10:00:00+05:30", "details": {}},
        ]
        self.loaded = [{"name": "qwen3.5:4b", "model": "qwen3.5:4b", "size": 4_000_000_000,
                        "size_vram": 3_100_000_000, "expires_at": "2026-10-27T12:00:00+05:30"}]
        self.pull_delay = 0.0
        self.chat_bodies: list[dict] = []
        self.chat_reply: dict | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if self.path == "/api/version":
                    self._json(200, {"version": "0.30.8"})
                elif self.path == "/api/tags":
                    self._json(200, {"models": outer.models})
                elif self.path == "/api/ps":
                    self._json(200, {"models": outer.loaded})
                else:
                    self._json(404, {"error": "not found"})

            def do_DELETE(self):
                body = self._body()
                names = [m["name"] for m in outer.models]
                if body.get("model") in names:
                    outer.models = [m for m in outer.models if m["name"] != body["model"]]
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._json(404, {"error": f"model '{body.get('model')}' not found"})

            def do_POST(self):
                body = self._body()
                if self.path == "/api/chat":
                    outer.chat_bodies.append(body)
                    self._json(200, outer.chat_reply or {})
                elif self.path == "/api/pull":
                    self._pull(body["model"])
                else:
                    self._json(404, {"error": "not found"})

            def _chunk(self, obj):
                data = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def _pull(self, model):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    self._chunk({"status": "pulling manifest"})
                    if model == "missing:tag":
                        self._chunk({"error": "pull model manifest: file does not exist"})
                    else:
                        big, small = 1_000_000, 4_000
                        self._chunk({"status": "pulling 6a6a6a6a6a6a", "digest": DIGEST_BIG, "total": big})
                        for done in range(0, big + 1, 100_000):
                            time.sleep(outer.pull_delay)
                            self._chunk({"status": "pulling 6a6a6a6a6a6a", "digest": DIGEST_BIG,
                                         "total": big, "completed": done})
                        self._chunk({"status": "pulling b1b1b1b1b1b1", "digest": DIGEST_SMALL,
                                     "total": small, "completed": small})
                        for status in ("verifying sha256 digest", "writing manifest", "success"):
                            self._chunk({"status": status})
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_manager(url: str) -> ollama.OllamaManager:
    settings = Settings(Path(tempfile.mkdtemp()) / "app.json")
    settings.set("ollama_url", url)
    mgr = ollama.OllamaManager(SimpleNamespace(settings=settings))
    pump(lambda: mgr.status["checked"])  # the startup check
    return mgr


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeOllama()
        self.unit = mock.patch.object(ollama.OllamaManager, "_unit_active", return_value=False)
        self.unit.start()
        self.mgr = make_manager(self.srv.url)

    def tearDown(self):
        self.unit.stop()
        self.srv.close()

    def test_refresh_running_external(self):
        st = self.mgr.status
        self.assertTrue(st["running"])
        self.assertEqual(st["owner"], "external")
        self.assertEqual(st["version"], "0.30.8")
        self.assertEqual(st["models_source"], "server")
        self.assertEqual([m["name"] for m in st["models"]], ["qwen3.5:4b", "qwen3:4b-instruct"])
        self.assertEqual(st["loaded"][0]["size_vram"], 3_100_000_000)
        self.assertTrue(st["installed"])
        self.assertTrue(self.mgr.has_model("qwen3.5:4b"))
        self.assertFalse(self.mgr.has_model("gemma4:e4b"))

    def test_owner_app_when_unit_active(self):
        with mock.patch.object(ollama.OllamaManager, "_unit_active", return_value=True):
            got = []
            self.mgr.refresh(got.append)
            self.assertTrue(pump(lambda: got))
        self.assertEqual(self.mgr.status["owner"], "app")

    def test_status_changed_fires_on_change_only(self):
        fired = []
        self.mgr.connect("status-changed", lambda _m: fired.append(1))
        got = []
        self.mgr.refresh(got.append)
        pump(lambda: got)
        self.assertEqual(fired, [])  # nothing changed
        self.srv.loaded = []
        got.clear()
        self.mgr.refresh(got.append)
        pump(lambda: got)
        self.assertEqual(len(fired), 1)

    def test_stop_refuses_external(self):
        res = []
        with mock.patch.object(ollama, "_run") as run:
            self.mgr.stop(lambda ok, err: res.append((ok, err)))
            self.assertTrue(pump(lambda: res))
            run.assert_not_called()
        self.assertFalse(res[0][0])
        self.assertIn("outside the app", res[0][1])
        self.assertIn("outside the app", self.mgr.status["error"])
        self.mgr.clear_error()
        self.assertIsNone(self.mgr.status["error"])

    def test_pull_progress_and_success(self):
        self.srv.pull_delay = 0.03  # paced like a real download so throttled progress gets through
        progress, res = [], []
        self.mgr.pull("qwen3.5:0.8b", lambda d, t, s: progress.append((d, t, s)), lambda ok, e: res.append((ok, e)))
        self.assertTrue(pump(lambda: res))
        self.assertEqual(res, [(True, None)])
        downloading = [p for p in progress if p[2] == "Downloading"]
        self.assertTrue(downloading)
        self.assertEqual(progress[-1][2], "Done")
        self.assertEqual(progress[-1][:2], (1_004_000, 1_004_000))
        self.assertEqual([p[0] for p in progress], sorted(p[0] for p in progress))

    def test_pull_error(self):
        res = []
        self.mgr.pull("missing:tag", None, lambda ok, e: res.append((ok, e)))
        self.assertTrue(pump(lambda: res))
        self.assertEqual(res, [(False, "pull model manifest: file does not exist")])

    def test_pull_cancel(self):
        self.srv.pull_delay = 0.2
        res = []
        handle = self.mgr.pull("qwen3.5:0.8b", lambda d, t, s: d > 0 and handle.cancel(),
                               lambda ok, e: res.append((ok, e)))
        self.assertTrue(pump(lambda: res, timeout=8))
        self.assertEqual(res, [(False, "Cancelled")])

    def test_delete(self):
        res = []
        self.mgr.delete("qwen3:4b-instruct", lambda ok, e: res.append((ok, e)))
        self.assertTrue(pump(lambda: res))
        self.assertEqual(res, [(True, None)])
        pump(lambda: len(self.mgr.status["models"]) == 1)
        self.assertEqual([m["name"] for m in self.mgr.status["models"]], ["qwen3.5:4b"])
        res.clear()
        self.mgr.delete("nope:1b", lambda ok, e: res.append((ok, e)))
        self.assertTrue(pump(lambda: res))
        self.assertEqual(res, [(False, "model 'nope:1b' not found")])

    def test_decide_reads_letter_probabilities(self):
        lp = [{"token": "B", "logprob": math.log(0.80), "top_logprobs": [
            {"token": "B", "logprob": math.log(0.80)},
            {"token": " B", "logprob": math.log(0.05)},
            {"token": "A", "logprob": math.log(0.10)},
            {"token": "Sure", "logprob": math.log(0.03)},
            {"token": "D", "logprob": math.log(0.01)},  # not an offered option
        ]}]
        self.srv.chat_reply = {"model": "qwen3.5:0.8b", "message": {"role": "assistant", "content": "B"},
                               "logprobs": lp, "done": True}
        out = []
        self.mgr.decide("qwen3.5:0.8b", "Pick the command.", ["open app", "volume", "none of these"],
                        "turn it up", lambda r, e: out.append((r, e)), num_gpu=0)
        self.assertTrue(pump(lambda: out))
        result, err = out[0]
        self.assertIsNone(err)
        self.assertEqual((result["index"], result["letter"]), (1, "B"))
        self.assertAlmostEqual(result["mass"], 0.95, places=6)
        self.assertAlmostEqual(result["probs"]["B"], 0.85 / 0.95, places=6)
        self.assertAlmostEqual(sum(result["probs"].values()), 1.0, places=6)
        body = self.srv.chat_bodies[-1]
        self.assertIs(body["think"], False)
        self.assertIs(body["logprobs"], True)
        self.assertEqual(body["top_logprobs"], 5)
        self.assertEqual(body["options"], {"temperature": 0, "num_predict": 1, "num_gpu": 0})
        self.assertIn("C. none of these", body["messages"][0]["content"])
        self.assertEqual(body["messages"][1], {"role": "user", "content": "turn it up"})

    def test_decide_without_letters_is_an_error(self):
        self.srv.chat_reply = {"message": {"content": "Hello"},
                               "logprobs": [{"token": "Hello", "logprob": -0.1, "top_logprobs": [
                                   {"token": "Hello", "logprob": -0.1}]}]}
        out = []
        self.mgr.decide("m", "s", ["x", "y"], "u", lambda r, e: out.append((r, e)))
        self.assertTrue(pump(lambda: out))
        self.assertIsNone(out[0][0])
        self.assertIn("option letter", out[0][1])


class StoppedTests(unittest.TestCase):
    def test_refresh_with_nothing_listening(self):
        with mock.patch.object(ollama, "scan_manifests", return_value=[{"name": "x:1b", "size": 1}]):
            mgr = make_manager("http://127.0.0.1:9")
        st = mgr.status
        self.assertFalse(st["running"])
        self.assertIsNone(st["owner"])
        self.assertEqual(st["models_source"], "disk")
        self.assertEqual(st["models"][0]["name"], "x:1b")

    def test_start_refuses_remote_url(self):
        mgr = make_manager("http://192.0.2.1:11434")
        res = []
        mgr.start(lambda ok, err: res.append((ok, err)))
        self.assertTrue(pump(lambda: res, timeout=15))
        self.assertFalse(res[0][0])
        self.assertIn("another computer", res[0][1])


class HelperTests(unittest.TestCase):
    def test_normalize_and_version(self):
        self.assertEqual(ollama.normalize_tag("qwen3"), "qwen3:latest")
        self.assertEqual(ollama.normalize_tag("qwen3.5:4b"), "qwen3.5:4b")
        self.assertEqual(ollama.normalize_tag("host:5000/m"), "host:5000/m:latest")
        self.assertEqual(ollama.parse_version("Warning: could not connect\nWarning: client version is 0.30.8"),
                         "0.30.8")
        self.assertEqual(ollama.parse_version("ollama version is 0.40.11"), "0.40.11")
        self.assertIsNone(ollama.parse_version("nothing"))

    def test_split_url(self):
        self.assertEqual(ollama.split_url("http://127.0.0.1:11434"), ("127.0.0.1", 11434, False))
        self.assertEqual(ollama.split_url("localhost"), ("localhost", 11434, False))
        self.assertTrue(ollama.is_local_url("http://localhost:1234"))
        self.assertFalse(ollama.is_local_url("https://ollama.example.org"))

    def test_scan_manifests(self):
        root = Path(tempfile.mkdtemp())
        m = root / "manifests" / "registry.ollama.ai" / "library" / "qwen3.5" / "4b"
        m.parent.mkdir(parents=True)
        m.write_text(json.dumps({"layers": [{"size": 3_000}, {"size": 24}], "config": {"size": 7}}))
        hf = root / "manifests" / "hf.co" / "org" / "repo" / "Q4_K_M"
        hf.parent.mkdir(parents=True)
        hf.write_text(json.dumps({"layers": [{"size": 5}]}))
        (root / "manifests" / "registry.ollama.ai" / "library" / "broken").mkdir()
        (root / "manifests" / "registry.ollama.ai" / "library" / "broken" / "x").write_text("{nope")
        found = {m["name"]: m["size"] for m in ollama.scan_manifests(root)}
        self.assertEqual(found, {"qwen3.5:4b": 3_024, "hf.co/org/repo:Q4_K_M": 5})

    def test_install_asset(self):
        self.assertEqual(ollama.install_asset("x86_64")[0], "ollama-linux-amd64.tar.zst")
        self.assertEqual(ollama.install_asset("aarch64")[0], "ollama-linux-arm64.tar.zst")
        self.assertIsNone(ollama.install_asset("riscv64"))
        for name, size, sha in ollama.OLLAMA_ASSETS.values():
            self.assertGreater(size, 100_000_000)
            self.assertRegex(sha, r"^[0-9a-f]{64}$")


class InstallTests(unittest.TestCase):
    """install_release with a tiny tar.zst served locally, in place of the 1.5 GB release."""

    def setUp(self):
        import compression.zstd as zstd

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            script = b"#!/bin/sh\necho 'ollama version is 0.40.11'\n"
            info = tarfile.TarInfo("bin/ollama")
            info.size, info.mode = len(script), 0o755
            tf.addfile(info, io.BytesIO(script))
            lib = b"\x7fELF fake"
            info = tarfile.TarInfo("lib/ollama/libggml-base.so.0")
            info.size = len(lib)
            tf.addfile(info, io.BytesIO(lib))
        self.archive = zstd.compress(buf.getvalue())
        self.sha = hashlib.sha256(self.archive).hexdigest()
        archive = self.archive

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(archive)))
                self.end_headers()
                self.wfile.write(archive)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/ollama-linux-amd64.tar.zst"
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(ollama, "MANAGED_DIR", self.tmp / "data" / "ollama"),
                        mock.patch.object(ollama, "CACHE_DIR", self.tmp / "cache")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_install_verifies_and_unpacks(self):
        stages = []
        ok, err = ollama.install_release(lambda d, t, s: stages.append(s), threading.Event(),
                                         asset=("ollama-linux-amd64.tar.zst", len(self.archive), self.sha),
                                         url=self.url)
        self.assertEqual((ok, err), (True, None))
        binary = self.tmp / "data" / "ollama" / ollama.OLLAMA_PIN / "bin" / "ollama"
        self.assertTrue(binary.is_file())
        self.assertTrue(binary.stat().st_mode & 0o100)
        self.assertTrue((binary.parents[1] / "lib" / "ollama" / "libggml-base.so.0").is_file())
        self.assertIn("Downloading", stages)
        self.assertEqual(stages[-1], "Done")
        self.assertFalse(list((self.tmp / "cache").glob("*.zst")))  # archive removed after unpacking
        with mock.patch.object(ollama.shutil, "which", return_value=None):
            self.assertEqual(ollama.find_binary(), str(binary))

    def test_install_rejects_bad_checksum(self):
        ok, err = ollama.install_release(lambda *a: None, threading.Event(),
                                         asset=("ollama-linux-amd64.tar.zst", len(self.archive), "0" * 64),
                                         url=self.url)
        self.assertFalse(ok)
        self.assertIn("Checksum", err)
        self.assertFalse((self.tmp / "data" / "ollama" / ollama.OLLAMA_PIN).exists())


class LetterProbTests(unittest.TestCase):
    def test_letter_tokens(self):
        lp = [{"token": "A", "top_logprobs": [
            {"token": "(A)", "logprob": math.log(0.5)}, {"token": "B.", "logprob": math.log(0.25)},
            {"token": "a", "logprob": math.log(0.2)}]}]
        probs = ollama.letter_probs(lp, 2)
        self.assertAlmostEqual(probs["A"], 0.5)
        self.assertAlmostEqual(probs["B"], 0.25)
        self.assertEqual(ollama.letter_probs(None, 3), {"A": 0.0, "B": 0.0, "C": 0.0})

    def test_logprobs_inside_message(self):
        reply = {"message": {"content": "A", "logprobs": [{"token": "A", "logprob": 0.0}]}}
        self.assertEqual(ollama.decision_from_reply(reply, 2)["letter"], "A")


if __name__ == "__main__":
    unittest.main()
