"""Parakeet speech-to-text (ONNX, CPU). Adapted from GLaDOS (MIT), see LICENSE.GLaDOS."""

from __future__ import annotations


def get_transcriber(engine: str = "parakeet-tdt"):
    if engine in ("tdt", "parakeet-tdt"):
        from .tdt_asr import AudioTranscriber
    elif engine in ("ctc", "parakeet-ctc"):
        from .ctc_asr import AudioTranscriber
    else:
        raise ValueError(f"unknown speech-to-text engine {engine!r}")
    return AudioTranscriber()
