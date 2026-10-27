"""The small decision models the router can use, by catalog id. Each has ask(state, question, options) -> probs,
max_options and device."""

from __future__ import annotations


def load(model_id: str, device: str = "GPU"):
    if model_id == "jiwo-0.8b":
        from ai_linux.engine import jiwo
        if not jiwo.installed():
            raise FileNotFoundError(jiwo.JIWO_DIR)
        return jiwo.Jiwo(device=device)
    if model_id == "laya":
        from ai_linux.engine import laya
        if not laya.installed():
            raise FileNotFoundError(laya.LAYA_DIR)
        return laya.Laya(device=device)
    raise FileNotFoundError(f"unknown small model {model_id!r}")
