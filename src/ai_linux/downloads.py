"""Resumable, verified downloads (stdlib only) and pinned Hugging Face file sets.

Every public call runs in a daemon thread; callbacks reach the GTK main thread through
``dispatch`` (GLib.idle_add by default). ``fetch`` is the synchronous core other workers reuse.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ai_linux.config import MODELS_DIR, REPO_ROOT

log = logging.getLogger(__name__)

CANCELLED = "Cancelled"
CHUNK = 256 * 1024
PROGRESS_EVERY_S = 0.1  # keeps the main loop from drowning in progress callbacks
USER_AGENT = "ai-linux/3 (local voice assistant)"
HF_BASE = "https://huggingface.co"

_OPENER = urllib.request.build_opener()


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    def __init__(self):
        super().__init__(CANCELLED)


@dataclass(frozen=True)
class HFFile:
    path: str  # path inside the repo, also the path under dest_dir
    size: int | None = None
    sha256: str | None = None


JIWO_REPO = "itsDigvijaysing/jiwo-0.8b-openvino-int8"
JIWO_PUBLISHED = True
JIWO_REVISION = "801c88f19bf56072c7030b30406e463716dcd389"  # the upload the hashes below are of
JIWO_DIR = MODELS_DIR / "jiwo-0.8b-openvino-int8"
JIWO_FILES: tuple[HFFile, ...] = (
    HFFile("openvino_model.xml", 2909350, "8498f05472f8ff3ca22f3f67e1aff95af127d6b95c2be27a0cffbc643c7223de"),
    HFFile("openvino_model.bin", 755100494, "0b0199d5650d143cc3a8c21b5ab5877702bb6bcc6ad8cdf8eb9386ae514ab92c"),
    HFFile("tokenizer.json", 19989493, "b6d27c11283798debbfbcb1bd3bbdbcebac6c9b20d0be0c5b79231c97de97501"),
    HFFile("decision_config.json", 5572, "f4743db5cf4d1afb2e7886c362b6fd53af20d4f1f712265d2a81bd0298f6ed35"),
    HFFile("LICENSE", 11343, "50cbab8a892c5f2993b8c7351a99182507472def3b1374558308605d99b86b32"),
)


def _idle_dispatch(fn: Callable, *args) -> None:
    from gi.repository import GLib

    GLib.idle_add(lambda: (fn(*args), False)[1])


def _call(dispatch, fn, *args) -> None:
    if fn is not None:
        (dispatch or _idle_dispatch)(fn, *args)


class _Throttle:
    """Forwards progress at most every PROGRESS_EVERY_S, always forwarding the final value."""

    def __init__(self, fn: Callable[[int, int], None] | None):
        self.fn = fn
        self.last = 0.0

    def __call__(self, done: int, total: int, force: bool = False) -> None:
        if self.fn is None:
            return
        now = time.monotonic()
        if force or now - self.last >= PROGRESS_EVERY_S:
            self.last = now
            self.fn(done, total)


def _hash_file(path: Path, hasher, cancel_event: threading.Event | None) -> None:
    with open(path, "rb") as f:
        while chunk := f.read(4 * CHUNK):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            hasher.update(chunk)


def _part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def _finish(part: Path, dest: Path, sha256: str | None, hasher) -> None:
    if sha256 and hasher.hexdigest() != sha256.lower():
        part.unlink(missing_ok=True)  # corrupt bytes must not be resumed
        raise DownloadError(f"Checksum mismatch for {dest.name}; the partial file was removed, try again")
    os.replace(part, dest)


def fetch(
    url: str,
    dest: str | os.PathLike,
    sha256: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
    expected_size: int | None = None,
    timeout: float = 30,
) -> int:
    """Download ``url`` to ``dest`` in the calling thread; returns the byte count.

    Resumes an existing ``dest.part`` with an HTTP Range request, verifies SHA-256 and renames
    atomically. Raises ``Cancelled`` (the .part is kept for resume) or ``DownloadError``.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    report = _Throttle(progress)
    for _attempt in range(2):  # the second pass restarts from zero when a resume is impossible
        offset = part.stat().st_size if part.exists() else 0
        hasher = hashlib.sha256() if sha256 else None
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            resp = _OPENER.open(urllib.request.Request(url, headers=headers), timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                # Range past the end: the .part may already be the whole file.
                if expected_size in (None, offset):
                    if hasher is not None:
                        _hash_file(part, hasher, cancel_event)
                    if hasher is None or hasher.hexdigest() == sha256.lower():
                        os.replace(part, dest)
                        report(offset, offset, force=True)
                        return offset
                part.unlink(missing_ok=True)
                continue
            raise DownloadError(f"Server answered HTTP {exc.code} for {dest.name}") from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise DownloadError(f"Could not reach the server: {reason}") from exc

        with resp:
            if offset and resp.status == 206:
                start, total = _content_range(resp.headers.get("Content-Range", ""))
                if start != offset:
                    part.unlink(missing_ok=True)
                    continue
                mode = "ab"
            else:
                offset, mode = 0, "wb"  # server ignored the range: start over
                length = resp.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else 0
            total = total or expected_size or 0
            if hasher is not None and offset:
                _hash_file(part, hasher, cancel_event)
            done = offset
            report(done, total, force=True)
            try:
                with open(part, mode) as f:
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise Cancelled()
                        try:
                            chunk = resp.read(CHUNK)
                        except http.client.IncompleteRead as exc:
                            chunk = exc.partial  # keep what arrived so the resume starts after it
                            if not chunk:
                                raise
                        if not chunk:
                            break
                        f.write(chunk)
                        if hasher is not None:
                            hasher.update(chunk)
                        done += len(chunk)
                        report(done, total)
                    f.flush()
                    os.fsync(f.fileno())  # this laptop can lose power abruptly
            except (http.client.HTTPException, OSError) as exc:
                raise DownloadError(f"Connection lost while downloading {dest.name}; try again to resume") from exc
        if total and done < total:
            raise DownloadError(f"Connection closed early for {dest.name}; try again to resume")
        _finish(part, dest, sha256, hasher)
        report(done, total or done, force=True)
        return done
    raise DownloadError(f"The server would not resume {dest.name}")


def _content_range(value: str) -> tuple[int, int]:
    """'bytes 100-199/1000' -> (100, 1000); unknown parts come back as -1 / 0."""
    try:
        unit, _, rest = value.partition(" ")
        span, _, total = rest.partition("/")
        start = int(span.split("-")[0]) if unit == "bytes" else -1
        return start, int(total) if total.isdigit() else 0
    except ValueError:
        return -1, 0


def download(
    url: str,
    dest: str | os.PathLike,
    sha256: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_done: Callable[[bool, str | None], None] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    expected_size: int | None = None,
    dispatch: Callable | None = None,
) -> threading.Thread:
    """Threaded ``fetch``: on_progress(done, total) and on_done(ok, error) arrive via ``dispatch``."""

    def worker():
        try:
            fetch(url, dest, sha256, lambda d, t: _call(dispatch, on_progress, d, t), cancel_event, expected_size)
        except Cancelled:
            _call(dispatch, on_done, False, CANCELLED)
        except DownloadError as exc:
            log.warning("download %s failed: %s", url, exc)
            _call(dispatch, on_done, False, str(exc))
        except Exception as exc:  # never leave the UI waiting
            log.exception("download %s crashed", url)
            _call(dispatch, on_done, False, f"Download failed: {exc}")
        else:
            _call(dispatch, on_done, True, None)

    thread = threading.Thread(target=worker, name=f"download {Path(dest).name}", daemon=True)
    thread.start()
    return thread


def _safe_target(dest_dir: Path, rel: str) -> Path:
    target = (dest_dir / rel).resolve()
    if rel.startswith(("/", "\\")) or not target.is_relative_to(dest_dir.resolve()):
        raise DownloadError(f"Refusing a file path outside the model folder: {rel}")
    return target


def _remote_size(url: str, timeout: float = 20) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            for key in ("X-Linked-Size", "Content-Length"):
                value = resp.headers.get(key)
                if value and value.isdigit():
                    return int(value)
    except (urllib.error.URLError, OSError) as exc:
        log.info("size check for %s failed: %s", url, exc)
    return None


def hf_url(repo: str, path: str, revision: str = "main", base_url: str = HF_BASE) -> str:
    return f"{base_url}/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"


def total_size(files) -> int:
    return sum((f.size or 0) for f in _specs(files))


def _specs(files) -> list[HFFile]:
    return [f if isinstance(f, HFFile) else HFFile(str(f)) for f in files]


def is_complete(dest_dir: str | os.PathLike, files) -> bool:
    """True when every file exists at its expected size (hashes were checked at download time)."""
    dest_dir = Path(dest_dir)
    for spec in _specs(files):
        path = dest_dir / spec.path
        try:
            if spec.size is not None and path.stat().st_size != spec.size:
                return False
            if spec.size is None and not path.is_file():
                return False
        except OSError:
            return False
    return True


def hf_files(
    repo: str,
    files,
    dest_dir: str | os.PathLike,
    on_progress: Callable[[int, int], None] | None = None,
    on_done: Callable[[bool, str | None], None] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    revision: str = "main",
    base_url: str = HF_BASE,
    dispatch: Callable | None = None,
    url_for: Callable[[str], str] | None = None,
) -> threading.Thread:
    """Download repo files into ``dest_dir`` one by one with combined progress.

    ``files`` holds repo paths or ``HFFile`` specs; complete files are skipped, so a retry
    resumes where the last attempt stopped.
    """
    dest_dir = Path(dest_dir)
    url_for = url_for or (lambda path: hf_url(repo, path, revision, base_url))

    def worker():
        try:
            specs = _specs(files)
            targets = [_safe_target(dest_dir, s.path) for s in specs]
            sizes = [s.size if s.size is not None else _remote_size(url_for(s.path))
                     for s in specs]
            total = sum(x or 0 for x in sizes)
            have = sum(t.stat().st_size for t in targets if t.exists())
            dest_dir.mkdir(parents=True, exist_ok=True)
            if total and shutil.disk_usage(dest_dir).free < (total - have) * 1.05:
                raise DownloadError("Not enough free disk space for this model")
            base = 0
            for spec, target, size in zip(specs, targets, sizes):
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                if size is not None and target.exists() and target.stat().st_size == size:
                    base += size
                    _call(dispatch, on_progress, base, total)
                    continue
                got = fetch(
                    url_for(spec.path), target, spec.sha256,
                    lambda d, _t, b=base: _call(dispatch, on_progress, b + d, max(total, b + d)),
                    cancel_event, size,
                )
                base += size if size is not None else got
            _call(dispatch, on_progress, base, max(total, base))
        except Cancelled:
            _call(dispatch, on_done, False, CANCELLED)
        except DownloadError as exc:
            log.warning("hf download %s failed: %s", repo, exc)
            _call(dispatch, on_done, False, str(exc))
        except Exception as exc:
            log.exception("hf download %s crashed", repo)
            _call(dispatch, on_done, False, f"Download failed: {exc}")
        else:
            _call(dispatch, on_done, True, None)

    thread = threading.Thread(target=worker, name=f"hf {repo}", daemon=True)
    thread.start()
    return thread


# Speech recognition weights from the GLaDOS release (MIT; models: NVIDIA Parakeet CC-BY-4.0, Silero VAD MIT).
SPEECH_BASE = "https://github.com/dnhkng/GLaDOS/releases/download/0.1/"
SPEECH_DIR = MODELS_DIR / "ASR"
_VAD = HFFile("silero_vad_16k_op15.onnx", 1289603, "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49")
SPEECH_FILES: dict[str, tuple[HFFile, ...]] = {
    "parakeet-tdt": (
        HFFile("parakeet-tdt-0.6b-v3_encoder.onnx", 1239032190,
               "e40d5963414174629ce6585192f2bad5dcdac7b0e18dcf05abdc0965a114197c"),
        HFFile("parakeet-tdt-0.6b-v3_decoder.onnx", 23620805,
               "8523d9c6ee4b6059f904c358177691a674dbc3902ef3d776c6c7cab9ea22c071"),
        HFFile("parakeet-tdt-0.6b-v3_joiner.onnx", 12645081,
               "e22366c5c222c21d1a88083d04536fd314fca441b5fbbfe8a7c600f218736557"),
        _VAD),
    "parakeet-ctc": (
        HFFile("nemo-parakeet_tdt_ctc_110m.onnx", 458096620,
               "313705ff6f897696ddbe0d92b5ffadad7429a47d2ddeef370e6f59248b1e8fb5"),
        _VAD),
}


def speech_installed(engine: str, dest_dir: Path | None = None) -> bool:
    """True when the weights are in the data dir or in a source checkout (development)."""
    files = SPEECH_FILES.get(engine, ())
    return bool(files) and any(is_complete(d, files) for d in (dest_dir or SPEECH_DIR, REPO_ROOT / "models/ASR"))


def speech_download(engine: str, on_progress=None, on_done=None, cancel_event=None, *, dest_dir=None,
                    dispatch=None):
    return hf_files("speech", SPEECH_FILES[engine], dest_dir or SPEECH_DIR, on_progress, on_done, cancel_event,
                    dispatch=dispatch, url_for=lambda path: SPEECH_BASE + path)


def jiwo_installed(dest_dir: Path | None = None) -> bool:
    return is_complete(dest_dir or JIWO_DIR, JIWO_FILES)


def jiwo_download(on_progress=None, on_done=None, cancel_event=None, *, dest_dir=None, dispatch=None):
    return hf_files(JIWO_REPO, JIWO_FILES, dest_dir or JIWO_DIR, on_progress, on_done, cancel_event,
                    revision=JIWO_REVISION, dispatch=dispatch)


def remove_dir(path: str | os.PathLike, on_done=None, *, dispatch=None) -> threading.Thread:
    """Delete a downloaded model folder off the main thread; only paths under MODELS_DIR."""

    def worker():
        target = Path(path).resolve()
        if not target.is_relative_to(MODELS_DIR.resolve()) or target == MODELS_DIR.resolve():
            _call(dispatch, on_done, False, "Refusing to delete outside the models folder")
            return
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass
        except OSError as exc:
            _call(dispatch, on_done, False, str(exc))
            return
        _call(dispatch, on_done, True, None)

    thread = threading.Thread(target=worker, name="remove model", daemon=True)
    thread.start()
    return thread
