"""ONNX Runtime settings for the CPU models: speech recognition and the voice.

Measured 27 Oct 2026 (i7-12700H): with the defaults every session used all 20 threads and busy-waited, so
Parakeet TDT took 0.79 s and 14.5 s of CPU per 4.5 s sentence and burned as much again while idle. With 4 threads
and no busy-waiting: 0.25 s, 0.9 s of CPU, nothing while idle. SuperTonic: 0.40 s instead of 0.81 s, 0.8 s of CPU
instead of 15 s.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import onnxruntime as ort

NO_SPIN = ("session.intra_op.allow_spinning", "0")


def threads(default: int) -> int:
    """AI_LINUX_CPU_THREADS overrides the per-model default."""
    value = os.environ.get("AI_LINUX_CPU_THREADS", "")
    return int(value) if value.isdigit() and int(value) > 0 else default


def session_options(n: int) -> ort.SessionOptions:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = n
    opts.inter_op_num_threads = 1
    opts.add_session_config_entry(*NO_SPIN)
    return opts


@contextmanager
def no_spinning() -> Iterator[None]:
    """For libraries that make their own SessionOptions (SuperTonic): no busy-waiting in sessions made inside."""
    real = ort.SessionOptions

    def make():
        opts = real()
        opts.add_session_config_entry(*NO_SPIN)
        return opts

    ort.SessionOptions = make
    try:
        yield
    finally:
        ort.SessionOptions = real
