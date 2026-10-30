"""Parakeet speech-to-text (ONNX; the TDT encoder can use the Intel GPU). Adapted from GLaDOS (MIT): LICENSE.GLaDOS."""

from __future__ import annotations


def get_transcriber(engine: str = "parakeet-tdt", device: str = "cpu"):
    """``device`` intel-gpu puts TDT's encoder on the Intel GPU (falls back to the CPU); CTC always uses the CPU."""
    if engine in ("tdt", "parakeet-tdt"):
        from .tdt_asr import AudioTranscriber

        return AudioTranscriber(device=device)
    if engine in ("ctc", "parakeet-ctc"):
        from .ctc_asr import AudioTranscriber

        return AudioTranscriber()
    raise ValueError(f"unknown speech-to-text engine {engine!r}")
