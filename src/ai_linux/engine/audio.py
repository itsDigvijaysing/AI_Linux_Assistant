"""PipeWire audio for the engine: the plain default mic through pw-record, sound through pw-play.

Nothing is loaded into PipeWire. Our own voice is taken out inside the engine (``echo.py``), only while we speak. The
echo-cancel module this replaced put all listening behind a 10 ms real-time chain; in power saver that chain lost most
of what was said even with nothing playing (2026-10-31, bench/README.md).
"""

from __future__ import annotations

import os
import queue
import secrets
import subprocess
import threading
import time

from ai_linux.engine.log import logger
import numpy as np
from numpy.typing import NDArray

from ai_linux.engine import echo
from ai_linux.engine.vad import VAD


class PipeWireAudioIO:
    """AudioProtocol backend over PipeWire (pw-record / pw-play)."""

    SAMPLE_RATE: int = 16000
    VAD_SIZE: int = 32          # ms per VAD frame -> 512 samples @ 16k
    VAD_THRESHOLD: float = 0.8  # balance: catch the user's barge-in vs. ignore AEC residual (GLADOS_VAD_THRESHOLD tunes it)

    def __init__(self, vad_threshold: float | None = None) -> None:
        self._vad_model = VAD()
        env_vad = os.environ.get("GLADOS_VAD_THRESHOLD", "").strip()
        if env_vad:
            try:
                vad_threshold = float(env_vad)
            except ValueError:
                pass
        self.vad_threshold = self.VAD_THRESHOLD if vad_threshold is None else vad_threshold
        self._sample_queue: queue.Queue[tuple[NDArray[np.float32], bool]] = queue.Queue()

        self._echo = echo.load()
        self._late: tuple | None = None  # a sound that started with the mic closed: (samples, rate, when)

        # capture state: `input_stream` mirrors the sounddevice attr the overlay bridge checks for "listening"
        self.input_stream: object | None = None
        self._rec_proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stop_rec = threading.Event()

        # playback state (mirrors sounddevice_io contract)
        self._pending_audio: NDArray[np.float32] | None = None
        self._pending_sample_rate: int = self.SAMPLE_RATE
        self._is_playing = False
        self._stop_event = threading.Event()
        self._play_proc: subprocess.Popen | None = None

        self._tmpdir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"

    @property
    def _aec_ok(self) -> bool:
        """False means no barge-in: the engine doesn't listen while it speaks."""
        return self._echo is not None and self._echo.ok

    def hear_own(self, sound: NDArray[np.float32], rate: int) -> None:
        """A sound of ours starts now. With the mic closed nothing would pace it, so only what is left when it opens."""
        if self._echo is None:
            return
        if self._rec_proc is None:
            self._late = (sound, rate, time.monotonic())
            return
        self._echo.feed(sound, rate)

    def stop_own(self) -> None:
        self._late = None
        if self._echo is not None:
            self._echo.stop()

    def _feed_late(self) -> None:
        late, self._late = self._late, None
        if late is not None and self._echo is not None:
            sound, rate, began = late
            left = sound[int((time.monotonic() - began) * rate):]
            if left.size:
                self._echo.feed(left, rate)

    def get_sample_queue(self) -> queue.Queue[tuple[NDArray[np.float32], bool]]:
        return self._sample_queue

    def start_listening(self) -> None:
        if self._rec_proc is not None or (self._reader is not None and self._reader.is_alive()):
            self.stop_listening()
        self._stop_rec.clear()
        if self._echo is not None:
            self._echo.reset()  # a sound that ended while the mic was closed left its tail behind
        while True:  # drop stale frames captured before the last stop so they can't leak into this session
            try:
                self._sample_queue.get_nowait()
            except queue.Empty:
                break
        # --container raw -> headerless PCM on stdout
        cmd = ["pw-record", "--container", "raw", "--rate", str(self.SAMPLE_RATE), "--channels", "1", "--format", "s16",
               "-"]
        try:
            self._rec_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except FileNotFoundError as exc:
            raise RuntimeError(f"pw-record not found ({exc}); install pipewire utils") from exc
        self.input_stream = self._rec_proc
        self._feed_late()
        self._reader = threading.Thread(target=self._read_loop, name="PWCapture", daemon=True)
        self._reader.start()
        logger.success("PipeWire capture started")

    def _read_loop(self) -> None:
        frame = int(self.SAMPLE_RATE * self.VAD_SIZE / 1000)  # 512 samples
        nbytes = frame * 2  # s16 = 2 bytes/sample
        buf = b""
        bad = 0  # consecutive frame failures: isolated glitches drop the frame, persistent failure stops
        stream = self._rec_proc.stdout if self._rec_proc else None
        if stream is None:
            return
        while not self._stop_rec.is_set():
            try:
                chunk = stream.read(nbytes)
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= nbytes:
                raw, buf = buf[:nbytes], buf[nbytes:]
                try:
                    data = (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)
                    if self._echo is not None:
                        data = self._echo.clean(data)
                    vad = self._vad_model(np.expand_dims(data, 0))
                    self._sample_queue.put((data.copy(), float(vad)))  # the engine picks the threshold
                    bad = 0
                except Exception as exc:  # noqa: BLE001 - a bad frame must not silently kill capture
                    bad += 1
                    logger.warning("PWCapture: VAD/emit failed ({}); dropping frame ({}/10)", exc, bad)
                    if bad >= 10:
                        logger.error("PWCapture: 10 consecutive frame failures; stopping reader")
                        return

    def stop_listening(self) -> None:
        self._stop_rec.set()
        if self._rec_proc is not None:
            try:
                self._rec_proc.terminate()
                self._rec_proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                try:
                    self._rec_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._rec_proc = None
        # Join the reader before a restart clears _stop_rec, so a quick stop->start never runs two
        # readers on one queue. Keep the reference if the join times out, so the next start re-joins it.
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.5)
        if reader is None or not reader.is_alive():
            self._reader = None
        self.input_stream = None
        if self._echo is not None:
            self._echo.reset()

    def start_speaking(self, audio_data: NDArray[np.float32], sample_rate: int | None = None, text: str = "") -> None:
        if not isinstance(audio_data, np.ndarray) or audio_data.size == 0:
            raise ValueError("Invalid audio data")
        self.stop_speaking()
        self._stop_event = threading.Event()
        self._is_playing = True
        self._pending_audio = audio_data
        self._pending_sample_rate = sample_rate or self.SAMPLE_RATE

    def measure_percentage_spoken(self, total_samples: int, sample_rate: int | None = None) -> tuple[bool, int]:
        import soundfile as sf  # core dep; lazy import keeps backend load cheap

        audio = self._pending_audio
        if audio is None:
            return False, -1  # sentinel: nothing queued, nothing played
        sr = sample_rate or self._pending_sample_rate
        stop = self._stop_event
        # pw-play rejects raw PCM on stdin, so write a temp WAV on tmpfs and play the file. It blocks
        # for the clip's duration, which is the playback monitoring the engine needs for barge-in.
        path = os.path.join(self._tmpdir, f"ai_tts_{secrets.token_hex(8)}.wav")  # unpredictable temp name
        try:
            sf.write(path, np.clip(audio, -1.0, 1.0).astype(np.float32), sr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS temp write failed ({}); audio dropped", exc)
            self._is_playing = False
            return False, -1  # sentinel: nothing played (see measure_percentage_spoken contract)
        cmd = ["pw-play", path]
        dur = len(audio) / sr if sr else 0.0
        interrupted = False
        dropped = False
        timed_out = False
        play_rc: int | None = None
        t0 = time.monotonic()
        try:
            self.hear_own(audio, sr)  # just before it plays: the reference may lead the echo, never follow it
            self._play_proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            while self._play_proc.poll() is None:
                if stop.is_set():
                    interrupted = True
                    self._play_proc.terminate()
                    break
                if time.monotonic() - t0 > dur + 5.0:  # absolute deadline: a hung pw-play must not stall the speak loop
                    timed_out = True
                    logger.warning("pw-play still running 5s past clip end; killing it")
                    self._play_proc.kill()
                    break
                time.sleep(0.02)  # ~20ms barge-in abort granularity
            if stop.is_set():  # stop_speaking() may have killed the proc before the check above ran
                interrupted = True
        except FileNotFoundError as exc:
            logger.warning("pw-play not found ({}); audio dropped", exc)
            dropped = True
        finally:
            p = self._play_proc
            if p is not None:
                try:
                    p.wait(timeout=0.5)
                except Exception:  # noqa: BLE001
                    try:
                        p.kill()
                    except Exception:  # noqa: BLE001
                        pass
                play_rc = p.returncode
            self._play_proc = None
            self._is_playing = False
            self.stop_own()
            try:
                os.unlink(path)
            except Exception:  # noqa: BLE001
                pass
        if dropped:
            return False, -1  # sentinel: player binary missing, nothing was heard
        if timed_out:
            return False, 100  # the clip's duration fully elapsed before the kill; count it as heard
        if interrupted:
            return True, (min(int((time.monotonic() - t0) / dur * 100), 100) if dur > 0 else 0)
        if play_rc not in (0, None):  # pw-play launched but exited non-zero (sink gone, ALSA busy) -> not heard
            logger.warning("pw-play exited {}; audio dropped", play_rc)
            return False, -1
        return False, 100

    def check_if_speaking(self) -> bool:
        return self._is_playing

    def stop_speaking(self) -> None:
        if self._is_playing:
            self._stop_event.set()
            self._is_playing = False
        p = self._play_proc
        if p is not None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
