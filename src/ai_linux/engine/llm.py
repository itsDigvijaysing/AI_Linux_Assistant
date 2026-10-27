"""Minimal Ollama client: streaming chat with tools and images, and one-token letter routing."""

from __future__ import annotations

import json
import math
import threading
import urllib.request
from collections.abc import Iterator

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class LLMError(Exception):
    pass


def _post(url: str, body: dict, timeout: float, stream: bool = False):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = json.loads(exc.read().decode()).get("error", "")
            except Exception:
                pass
        raise LLMError(detail or str(exc)) from exc


def chat(base: str, model: str, messages: list[dict], *, tools: list[dict] | None = None, think: bool = False,
         keep_alive: str | int = "10m", options: dict | None = None, cancel: threading.Event | None = None,
         timeout: float = 120) -> Iterator[tuple[str, object]]:
    """Yields ("text", str) pieces, then ("tools", [calls]) if any, then ("done", final chunk)."""
    body = {"model": model, "messages": messages, "stream": True, "think": think, "keep_alive": keep_alive,
            "options": {"temperature": 0.3, **(options or {})}}
    if tools:
        body["tools"] = tools
    resp = _post(f"{base}/api/chat", body, timeout)
    calls: list[dict] = []
    try:
        for line in resp:
            if cancel is not None and cancel.is_set():
                return
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                raise LLMError(chunk["error"])
            msg = chunk.get("message") or {}
            if msg.get("content"):
                yield "text", msg["content"]
            for c in msg.get("tool_calls") or []:
                fn = c.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                calls.append({"name": fn.get("name", ""), "arguments": args if isinstance(args, dict) else {}})
            if chunk.get("done"):
                if calls:
                    yield "tools", calls
                yield "done", chunk
                return
    finally:
        resp.close()


def letters(base: str, model: str, text: str, options: dict[str, str], instructions: str,
            keep_alive: str | int = "10m", timeout: float = 60, llm_options: dict | None = None) -> dict[str, float]:
    """Probability per option from the first answer token (letter labels, Ollama top logprobs)."""
    opts = list(options.items())[: len(LETTERS)]
    system = (f"{instructions} Answer with the letter of the best option only.\n"
              + "\n".join(f"{LETTERS[i]}) {n}: {d}" for i, (n, d) in enumerate(opts)))
    body = {"model": model, "stream": False, "think": False, "keep_alive": keep_alive, "logprobs": True,
            "top_logprobs": 20, "options": {**(llm_options or {}), "temperature": 0, "num_predict": 1},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
    with _post(f"{base}/api/chat", body, timeout) as resp:
        r = json.loads(resp.read())
    by_letter = {LETTERS[i]: n for i, (n, _) in enumerate(opts)}
    probs = {n: 0.0 for n, _ in opts}
    for t in (r.get("logprobs") or [{}])[0].get("top_logprobs", []):
        n = by_letter.get(str(t.get("token", "")).strip())
        if n:
            probs[n] += math.exp(t["logprob"])
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()} if total > 0 else probs


def loaded(base: str, model: str) -> bool:
    """Is the model in memory already? Errs towards yes (no cue) when Ollama doesn't answer quickly."""
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=0.5) as resp:
            names = {m.get("name") for m in json.loads(resp.read()).get("models") or []}
    except Exception:
        return True
    return model in names or f"{model}:latest" in names


def unload(base: str, model: str) -> None:
    try:
        _post(f"{base}/api/generate", {"model": model, "keep_alive": 0}, 10).close()
    except LLMError:
        pass
