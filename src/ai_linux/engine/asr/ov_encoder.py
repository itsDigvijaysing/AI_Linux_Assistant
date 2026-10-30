"""Parakeet's encoder on the Intel GPU through OpenVINO: same words as the CPU, 2.5x faster, a sixth of the CPU time.

Measured in bench/README.md ("Speech recognition: int8, the Intel GPU, live text"). The GPU compiles its code once
per input length (~0.6 s each, kept in the cache folder), so input is padded to whole seconds: at most 15 lengths.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from ai_linux.paths import CACHE_DIR

BUCKET = 100  # feature frames (10 ms each): pad to whole seconds


def pad_frames(features: np.ndarray, bucket: int = BUCKET) -> tuple[np.ndarray, int]:
    """Features padded with zeros on the time axis up to a multiple of ``bucket``, and the true length."""
    t = int(features.shape[2])
    pad = -t % bucket
    return (np.pad(features, ((0, 0), (0, 0), (0, pad))) if pad else features), t


class _Port:
    def __init__(self, name: str):
        self.name, self.shape, self.type = name, None, "tensor(float)"


class _Meta:
    def __init__(self, values: dict[str, str]):
        self.custom_metadata_map = values


class OVEncoder:
    """Stands in for the encoder's onnxruntime session inside _OnnxTDTModel."""

    bucket = BUCKET

    def __init__(self, path: Path, meta: dict[str, str]):
        from ai_linux.engine.jiwo import prepare_openvino

        prepare_openvino()
        import openvino as ov

        core = ov.Core()
        if "GPU" not in core.available_devices:
            raise RuntimeError("OpenVINO finds no Intel GPU")
        cache = CACHE_DIR / "openvino"
        cache.mkdir(parents=True, exist_ok=True)
        core.set_property({"CACHE_DIR": str(cache)})
        self._model = core.compile_model(str(path), "GPU", {"PERFORMANCE_HINT": "LATENCY"})
        self._req = self._model.create_infer_request()
        self._meta = _Meta(meta)
        self._lock = threading.Lock()  # one infer request: never run it from two threads

    def get_inputs(self) -> list[_Port]:
        return [_Port(i.get_any_name()) for i in self._model.inputs]

    def get_outputs(self) -> list[_Port]:
        return [_Port(o.get_any_name()) for o in self._model.outputs]

    def get_modelmeta(self) -> _Meta:
        return self._meta

    def run(self, names, feed: dict) -> list[np.ndarray]:
        with self._lock:
            res = self._req.infer(feed)
            outputs = self._model.outputs if names is None else self._model.outputs[:1]
            return [np.array(res[o]) for o in outputs]
