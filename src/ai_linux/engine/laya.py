"""Laya typed-decisions (convaiinnovations/laya, Apache-2.0) as our OpenVINO export, without torch.

Picks one option from a menu given at request time, with a calibrated probability per option. The sequence layout,
temperature buckets and temperature clamp are ported from Laya 0.4.15's common.py and onnx_agent.py (Apache-2.0,
Convai Innovations); only the "choice" question type, and options are never reordered, so Laya's unpermute step is
not needed. Needs openvino, tokenizers and numpy. Export and parity check: bench/router/laya_export.py.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ai_linux.paths import CACHE_DIR, MODELS_DIR

log = logging.getLogger(__name__)

NAME = "laya-typed-decisions-openvino"
LAYA_DIR = MODELS_DIR / NAME
FILES = ("openvino_model.xml", "openvino_model.bin", "tokenizer.json", "decision_config.json")
OPTION_TOKENS = 48  # Laya truncates each option's text to this many tokens
TEMP_MIN, TEMP_MAX = 0.5, 5.0  # Laya clamps every shipped temperature into this range


def installed(model_dir: Path = LAYA_DIR) -> bool:
    return all((Path(model_dir) / f).is_file() for f in FILES)


def clamp_temperature(t) -> float:
    if isinstance(t, bool):
        return 1.0
    try:
        t = float(t)
    except (TypeError, ValueError):
        return 1.0
    if t != t or t in (float("inf"), float("-inf")):
        return 1.0
    return min(TEMP_MAX, max(TEMP_MIN, t))


def temp_bucket(k: int) -> str:
    return "choice:" + ("2" if k <= 2 else "3-5" if k <= 5 else "6-10" if k <= 10 else "11+")


def render_option(name: str, desc) -> str:
    if desc is None or desc == "":
        return str(name)
    if not isinstance(desc, str):
        desc = json.dumps(desc, ensure_ascii=False, separators=(", ", ": "), default=str)
    return f"{name}: {desc}"


class Tok:
    """The few tokenizer calls Laya makes, on the plain `tokenizers` library (no transformers)."""

    def __init__(self, path: Path):
        from tokenizers import Tokenizer

        self._t = Tokenizer.from_file(str(path))
        self._t.no_truncation()
        self._t.no_padding()
        self.mask_token = "[MASK]"
        self.cls_id, self.sep_id, self.mask_id, self.pad_id = (
            self._t.token_to_id(t) for t in ("[CLS]", "[SEP]", "[MASK]", "[PAD]"))
        self._lock = threading.Lock()  # a Rust tokenizer object isn't safe to share across threads

    def encode(self, text: str, max_length: int | None = None) -> list[int]:
        with self._lock:
            ids = self._t.encode(text, add_special_tokens=False).ids
        return ids[:max_length] if max_length is not None else ids


def build_head(tok: Tok, question: str, options: dict, head_max_len: int) -> tuple[list[int], list[int]]:
    """[CLS] choice question: <instructions> [SEP] [MASK] opt0 [MASK] opt1 ... [SEP], and each [MASK] position."""
    mask = tok.mask_token
    head = tok.encode("choice question: %s" % str(question).replace(mask, " "))
    opts = [[tok.mask_id] + tok.encode(" " + render_option(n, d).replace(mask, " "), OPTION_TOKENS)
            for n, d in options.items()]
    budget = head_max_len - sum(len(o) for o in opts)
    if budget < 16:
        per = max(4, (head_max_len - 16) // max(1, len(opts)))
        opts = [o[:per] for o in opts]
        budget = head_max_len - sum(len(o) for o in opts)
    ids = [tok.cls_id] + head[: max(8, budget)] + [tok.sep_id]
    markers = []
    for o in opts:
        markers.append(len(ids))
        ids.extend(o)
    ids.append(tok.sep_id)
    return ids, markers


def build_sequence(tok: Tok, state: str, question: str, options: dict, max_len: int,
                   head_max_len: int) -> tuple[list[int], list[int]]:
    """The head, then the state clamped to the room left, then [SEP]; cut to max_len."""
    ids, markers = build_head(tok, question, options, head_max_len)
    room = max(0, max_len - len(ids) - 1)
    ids = ids + tok.encode(state.replace(tok.mask_token, " "))[:room] + [tok.sep_id]
    return ids[:max_len], [m for m in markers if m < max_len]


class Laya:
    def __init__(self, model_dir: Path = LAYA_DIR, device: str = "GPU"):
        from ai_linux.engine.jiwo import prepare_openvino

        prepare_openvino()
        import openvino as ov

        model_dir = Path(model_dir)
        cfg = json.loads((model_dir / "decision_config.json").read_text())
        self.max_len = int(cfg["max_len"])
        self.head_max_len = int(cfg["head_max_len"])
        self.max_options = int(cfg["max_options"])
        self.temp = clamp_temperature(cfg["temperature"][0])  # index 0 = the choice type
        self.temp_by_k = {k: clamp_temperature(v) for k, v in cfg["temperature_by_options"].items()}
        self.tok = Tok(model_dir / "tokenizer.json")
        core = ov.Core()
        cache = CACHE_DIR / "openvino"
        cache.mkdir(parents=True, exist_ok=True)
        core.set_property({"CACHE_DIR": str(cache)})
        self.device = device if device in core.available_devices else "CPU"
        model = core.compile_model(str(model_dir / "openvino_model.xml"), self.device)
        self._req = model.create_infer_request()
        self._lock = threading.Lock()
        log.info("laya loaded on %s", self.device)

    def ask(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        """Probability per option name, in menu order."""
        import numpy as np

        k = len(options)
        if not 1 < k <= self.max_options:
            raise ValueError(f"laya takes 2..{self.max_options} options, got {k}")
        ids, markers = build_sequence(self.tok, state, question, options, self.max_len, self.head_max_len)
        if len(markers) != k:
            raise ValueError(f"only {len(markers)} of {k} options fit in {self.max_len} tokens")
        mpos = np.array([markers], dtype=np.int64)
        feed = [np.array([ids], dtype=np.int64), np.ones((1, len(ids)), dtype=np.int64), mpos,
                np.ones_like(mpos, dtype=bool), np.zeros(1, dtype=np.int64)]
        with self._lock:
            logits = self._req.infer(feed)[0][0, :k].astype(np.float64)
        z = logits / self.temp_by_k.get(temp_bucket(k), self.temp)
        p = np.exp(z - z.max())
        p /= p.sum()
        return dict(zip(options, p.tolist()))
