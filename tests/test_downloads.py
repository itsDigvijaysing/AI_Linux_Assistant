"""downloads.py against a local fake server (Range, redirects, drops); never the real network."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux import downloads  # noqa: E402
from ai_linux.downloads import HFFile  # noqa: E402

BLOB = os.urandom(300_000) + b"tail"
SMALL = b'{"hello": "world"}\n'


def direct(fn, *args):
    fn(*args)


class FakeServer:
    """Files by path; options: ignore_range, drop_after (bytes), slow (s per 16 KB), redirect."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.ignore_range = False
        self.drop_after: int | None = None
        self.slow = 0.0
        self.log: list[tuple[str, str, str | None]] = []  # method, path, Range
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def _serve(self, head: bool):
                outer.log.append((self.command, self.path, self.headers.get("Range")))
                if self.path in outer.redirects:
                    self.send_response(302)
                    self.send_header("Location", outer.redirects[self.path])
                    self.send_header("X-Linked-Size", str(len(outer.files.get(outer.redirects[self.path], b""))))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = outer.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                rng = self.headers.get("Range")
                if rng and not outer.ignore_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if head:
                    return
                if outer.drop_after is not None and start == 0:
                    self.wfile.write(body[: outer.drop_after])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                for i in range(0, len(body), 16384):
                    if outer.slow:
                        time.sleep(outer.slow)
                    try:
                        self.wfile.write(body[i:i + 16384])
                    except (BrokenPipeError, ConnectionResetError):
                        return

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_download(*args, **kwargs):
    done = threading.Event()
    result = {}
    progress = []

    def on_done(ok, err):
        result.update(ok=ok, err=err)
        done.set()

    downloads.download(*args, on_progress=lambda d, t: progress.append((d, t)), on_done=on_done,
                       dispatch=direct, **kwargs)
    assert done.wait(20), "download timed out"
    return result, progress


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        self.srv.files["/blob"] = BLOB
        self.tmp = Path(tempfile.mkdtemp())
        self.sha = hashlib.sha256(BLOB).hexdigest()

    def tearDown(self):
        self.srv.close()

    def test_full_download_verified_and_atomic(self):
        dest = self.tmp / "sub" / "blob.bin"
        res, progress = run_download(f"{self.srv.url}/blob", dest, self.sha)
        self.assertEqual(res, {"ok": True, "err": None})
        self.assertEqual(dest.read_bytes(), BLOB)
        self.assertFalse((self.tmp / "sub" / "blob.bin.part").exists())
        self.assertEqual(progress[-1], (len(BLOB), len(BLOB)))
        self.assertEqual([d for d, _ in progress], sorted(d for d, _ in progress))

    def test_checksum_mismatch_removes_part(self):
        dest = self.tmp / "blob.bin"
        res, _ = run_download(f"{self.srv.url}/blob", dest, "0" * 64)
        self.assertFalse(res["ok"])
        self.assertIn("Checksum mismatch", res["err"])
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("blob.bin.part").exists())

    def test_resume_uses_range(self):
        dest = self.tmp / "blob.bin"
        dest.with_name("blob.bin.part").write_bytes(BLOB[:100_000])
        res, progress = run_download(f"{self.srv.url}/blob", dest, self.sha)
        self.assertTrue(res["ok"], res)
        self.assertEqual(dest.read_bytes(), BLOB)
        self.assertEqual(self.srv.log[-1][2], "bytes=100000-")
        self.assertEqual(progress[0][0], 100_000)  # progress starts at the resumed offset

    def test_server_ignoring_range_restarts_cleanly(self):
        self.srv.ignore_range = True
        dest = self.tmp / "blob.bin"
        dest.with_name("blob.bin.part").write_bytes(b"garbage" * 1000)
        res, _ = run_download(f"{self.srv.url}/blob", dest, self.sha)
        self.assertTrue(res["ok"], res)
        self.assertEqual(dest.read_bytes(), BLOB)

    def test_complete_part_answered_416(self):
        dest = self.tmp / "blob.bin"
        dest.with_name("blob.bin.part").write_bytes(BLOB)
        res, _ = run_download(f"{self.srv.url}/blob", dest, self.sha, expected_size=len(BLOB))
        self.assertTrue(res["ok"], res)
        self.assertEqual(dest.read_bytes(), BLOB)

    def test_dropped_connection_keeps_part_then_resumes(self):
        self.srv.drop_after = 50_000
        dest = self.tmp / "blob.bin"
        res, _ = run_download(f"{self.srv.url}/blob", dest, self.sha)
        self.assertFalse(res["ok"])
        self.assertIn("resume", res["err"])
        part = dest.with_name("blob.bin.part")
        self.assertEqual(part.stat().st_size, 50_000)
        self.srv.drop_after = None
        res, _ = run_download(f"{self.srv.url}/blob", dest, self.sha)
        self.assertTrue(res["ok"], res)
        self.assertEqual(dest.read_bytes(), BLOB)
        self.assertEqual(self.srv.log[-1][2], "bytes=50000-")

    def test_cancel_keeps_part(self):
        self.srv.slow = 0.05
        dest = self.tmp / "blob.bin"
        cancel = threading.Event()
        done = threading.Event()
        result = {}

        def on_progress(d, _t):
            if d > 30_000:
                cancel.set()

        downloads.download(f"{self.srv.url}/blob", dest, self.sha, on_progress,
                           lambda ok, err: (result.update(ok=ok, err=err), done.set()), cancel, dispatch=direct)
        self.assertTrue(done.wait(20))
        self.assertEqual(result, {"ok": False, "err": downloads.CANCELLED})
        self.assertFalse(dest.exists())
        self.assertTrue(dest.with_name("blob.bin.part").exists())

    def test_http_error_and_unreachable(self):
        res, _ = run_download(f"{self.srv.url}/missing", self.tmp / "x")
        self.assertFalse(res["ok"])
        self.assertIn("404", res["err"])
        res, _ = run_download("http://127.0.0.1:9/x", self.tmp / "y")
        self.assertFalse(res["ok"])
        self.assertIn("reach", res["err"])


class HFTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        self.tmp = Path(tempfile.mkdtemp())
        rev = "abc123"
        self.rev = rev
        # LFS files redirect to a CDN path, like huggingface.co does.
        self.srv.files["/cdn/model"] = BLOB
        self.srv.redirects[f"/org/repo/resolve/{rev}/model.safetensors"] = "/cdn/model"
        self.srv.files[f"/org/repo/resolve/{rev}/config.json"] = SMALL
        self.srv.files[f"/org/repo/resolve/{rev}/tokenizer/tokenizer.json"] = SMALL * 3
        self.specs = (
            HFFile("model.safetensors", len(BLOB), hashlib.sha256(BLOB).hexdigest()),
            HFFile("config.json", len(SMALL), hashlib.sha256(SMALL).hexdigest()),
            "tokenizer/tokenizer.json",  # size unknown: found with HEAD
        )

    def tearDown(self):
        self.srv.close()

    def _run(self, files, dest, cancel=None):
        done = threading.Event()
        result = {}
        progress = []
        downloads.hf_files("org/repo", files, dest, lambda d, t: progress.append((d, t)),
                           lambda ok, err: (result.update(ok=ok, err=err), done.set()), cancel,
                           revision=self.rev, base_url=self.srv.url, dispatch=direct)
        self.assertTrue(done.wait(20), "hf_files timed out")
        return result, progress

    def test_set_downloads_nested_with_combined_progress(self):
        dest = self.tmp / "laya"
        res, progress = self._run(self.specs, dest)
        self.assertEqual(res, {"ok": True, "err": None})
        self.assertEqual((dest / "model.safetensors").read_bytes(), BLOB)
        self.assertEqual((dest / "tokenizer" / "tokenizer.json").read_bytes(), SMALL * 3)
        total = len(BLOB) + len(SMALL) * 4
        self.assertEqual(progress[-1], (total, total))
        done_values = [d for d, _ in progress]
        self.assertEqual(done_values, sorted(done_values))
        self.assertTrue(downloads.is_complete(dest, self.specs[:2]))

    def test_complete_files_are_skipped(self):
        dest = self.tmp / "laya"
        self._run(self.specs, dest)
        gets_before = sum(1 for m, *_ in self.srv.log if m == "GET")
        res, _ = self._run(self.specs[:2], dest)
        self.assertTrue(res["ok"])
        self.assertEqual(sum(1 for m, *_ in self.srv.log if m == "GET"), gets_before)

    def test_bad_hash_fails_the_set(self):
        dest = self.tmp / "laya"
        res, _ = self._run((HFFile("config.json", len(SMALL), "f" * 64),), dest)
        self.assertFalse(res["ok"])
        self.assertFalse((dest / "config.json").exists())
        self.assertFalse(downloads.is_complete(dest, (HFFile("config.json", len(SMALL)),)))

    def test_path_traversal_refused(self):
        res, _ = self._run(("../../etc/passwd",), self.tmp / "laya")
        self.assertFalse(res["ok"])
        self.assertIn("outside", res["err"])

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        res, _ = self._run(self.specs, self.tmp / "laya", cancel)
        self.assertEqual(res, {"ok": False, "err": downloads.CANCELLED})


class JiwoSpecTests(unittest.TestCase):
    def test_jiwo_files(self):
        paths = {f.path for f in downloads.JIWO_FILES}
        self.assertEqual(paths, {"openvino_model.xml", "openvino_model.bin", "tokenizer.json",
                                 "decision_config.json", "LICENSE"})
        for f in downloads.JIWO_FILES:
            self.assertGreater(f.size, 0)
            self.assertRegex(f.sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(downloads.JIWO_DIR.name, "jiwo-0.8b-openvino-int8")
        self.assertRegex(downloads.JIWO_REVISION, r"^[0-9a-f]{40}$")  # pinned to the upload the hashes are of
        self.assertEqual(downloads.hf_url(downloads.JIWO_REPO, "tokenizer.json", downloads.JIWO_REVISION),
                         f"https://huggingface.co/itsDigvijaysing/jiwo-0.8b-openvino-int8/resolve/"
                         f"{downloads.JIWO_REVISION}/tokenizer.json")

    def test_content_range_parse(self):
        self.assertEqual(downloads._content_range("bytes 100-199/1000"), (100, 1000))
        self.assertEqual(downloads._content_range("bytes 5-9/*"), (5, 0))
        self.assertEqual(downloads._content_range("junk"), (-1, 0))

    def test_remove_dir_refuses_outside_models(self):
        done = threading.Event()
        result = {}
        downloads.remove_dir("/tmp", lambda ok, err: (result.update(ok=ok, err=err), done.set()), dispatch=direct)
        self.assertTrue(done.wait(5))
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
