"""Routers under test. Each takes the tool menu ({name: description}) at request time and returns
{option: probability}; none of them has a fixed tool list, so a new tool is just a new menu entry."""

import json
import math
import os
import sys
import urllib.request

sys.modules.setdefault("openvino_telemetry", None)  # importing openvino would send Intel an analytics event

INSTR = "Which tool should handle this spoken request to a Linux desktop assistant?"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _norm(d):
    s = sum(d.values()) or 1.0
    return {k: v / s for k, v in d.items()}


class QwenLetters:
    """Option letters in the prompt, one generated token, probabilities from Ollama's top logprobs."""

    def __init__(self, model, port):
        self.name, self.model, self.url = model, model, f"http://127.0.0.1:{port}/api/chat"

    def scores(self, text, menu):
        opts = list(menu.items())
        system = ("You route one spoken request for a Linux desktop assistant. Answer with the letter of the best "
                  "option only.\n" + "\n".join(f"{LETTERS[i]}) {n}: {d}" for i, (n, d) in enumerate(opts)))
        r = _post(self.url, {"model": self.model, "stream": False, "think": False, "keep_alive": "5m",
                             "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_predict": 1},
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]})
        by_letter = {LETTERS[i]: n for i, (n, _) in enumerate(opts)}
        probs = {n: 0.0 for n, _ in opts}
        for t in (r.get("logprobs") or [{}])[0].get("top_logprobs", []):
            n = by_letter.get(t["token"].strip())
            if n:
                probs[n] += math.exp(t["logprob"])
        return _norm(probs)

    def ask(self, text, options, instructions):
        opts = list(options.items())
        system = (f"{instructions} Answer with the letter of the best option only.\n"
                  + "\n".join(f"{LETTERS[i]}) {n}: {d}" for i, (n, d) in enumerate(opts)))
        r = _post(self.url, {"model": self.model, "stream": False, "think": False, "keep_alive": "5m",
                             "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_predict": 1},
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]})
        by_letter = {LETTERS[i]: n for i, (n, _) in enumerate(opts)}
        probs = {n: 0.0 for n, _ in opts}
        for t in (r.get("logprobs") or [{}])[0].get("top_logprobs", []):
            n = by_letter.get(t["token"].strip())
            if n:
                probs[n] += math.exp(t["logprob"])
        return _norm(probs)


class Laya:
    def __init__(self, subfolder=None, head_max_len=None):
        os.environ.setdefault("USE_TF", "0")
        import laya
        self.name = "laya" + (f"-{subfolder}" if subfolder else "") + (f"-h{head_max_len}" if head_max_len else "")
        self.agent = laya.load("convaiinnovations/laya", subfolder=subfolder) if subfolder else laya.load("convaiinnovations/laya")
        if head_max_len:
            self.agent.cfg["head_max_len"] = head_max_len

    def scores(self, text, menu):
        q = {"tool": {"type": "choice", "instructions": INSTR, "criteria": dict(menu)}}
        a = self.agent.predict(text, q)["answers"]["tool"]
        return _norm(dict(a["probabilities"])) if "probabilities" in a else {a["choice"]: a.get("confidence", 1.0)}


class Reranker:
    """Cross-encoder: scores (request, tool description) pairs; no 'none' option, a threshold decides."""

    def __init__(self, device="CPU", seq=64):
        import openvino as ov
        from huggingface_hub import hf_hub_download
        from transformers import AutoTokenizer
        repo = "Alibaba-NLP/gte-reranker-modernbert-base"
        self.name, self.seq = f"gte-reranker-int8-{device.lower()}", seq
        self.tok = AutoTokenizer.from_pretrained(repo)
        core = ov.Core()
        model = core.read_model(hf_hub_download(repo, "onnx/model_int8.onnx"))
        self.cm = core.compile_model(model, device)

    def scores(self, text, menu):
        names = [n for n in menu if n != "none"]
        enc = self.tok([text] * len(names), [menu[n] for n in names], padding="max_length", truncation=True,
                       max_length=self.seq, return_tensors="np")
        out = self.cm({"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]})
        logits = list(out.values())[0].reshape(-1)
        return {n: 1 / (1 + math.exp(-float(x))) for n, x in zip(names, logits)}


class Embed:
    """Embedding similarity (nomic-embed-text via Ollama); a threshold decides 'none'."""

    def __init__(self, port):
        self.name, self.url, self.cache = "nomic-embed", f"http://127.0.0.1:{port}/api/embed", {}

    def _emb(self, texts):
        return _post(self.url, {"model": "nomic-embed-text", "input": texts, "keep_alive": "5m"})["embeddings"]

    def scores(self, text, menu):
        names = [n for n in menu if n != "none"]
        missing = [n for n in names if (n, menu[n]) not in self.cache]
        if missing:
            for n, e in zip(missing, self._emb([f"search_document: {menu[n]}" for n in missing])):
                self.cache[(n, menu[n])] = e
        q = self._emb([f"search_query: {text}"])[0]
        qn = math.sqrt(sum(x * x for x in q))
        out = {}
        for n in names:
            d = self.cache[(n, menu[n])]
            out[n] = sum(a * b for a, b in zip(q, d)) / (qn * math.sqrt(sum(x * x for x in d)))
        return out


class Jiwo:
    def __init__(self, repo="eljiwo/jiwo-0.8b"):
        from jiwo.model import DecisionModel
        self.name, self.model = repo.split("/")[-1], DecisionModel.from_pretrained(repo)

    def scores(self, text, menu):
        q = {"tool": {"type": "choice", "instructions": INSTR, "criteria": dict(menu)}}
        return _norm(dict(self.model.decide(text, q)["answers"]["tool"]["probabilities"]))


class RerankerOV:
    """Same cross-encoder, using only openvino + tokenizers (no transformers), on CPU or the Intel GPU."""

    def __init__(self, device="CPU", seq=64):
        import glob
        import numpy as np
        import openvino as ov
        from tokenizers import Tokenizer
        snap = glob.glob(os.path.expanduser(
            "~/.cache/huggingface/hub/models--Alibaba-NLP--gte-reranker-modernbert-base/snapshots/*/"))[0]
        self.np, self.name = np, f"gte-reranker-int8-{device.lower()}"
        self.tok = Tokenizer.from_file(snap + "tokenizer.json")
        self.tok.enable_truncation(max_length=seq)
        self.tok.enable_padding(length=seq)
        core = ov.Core()
        model = core.read_model(snap + "onnx/model_int8.onnx")
        model.reshape({"input_ids": [-1, seq], "attention_mask": [-1, seq]})
        self.cm = core.compile_model(model, device)

    def scores(self, text, menu):
        names = [n for n in menu if n != "none"]
        enc = self.tok.encode_batch([(text, menu[n]) for n in names])
        ids = self.np.array([e.ids for e in enc], dtype=self.np.int64)
        mask = self.np.array([e.attention_mask for e in enc], dtype=self.np.int64)
        logits = self.cm({"input_ids": ids, "attention_mask": mask})[0].reshape(-1)
        return {n: 1 / (1 + math.exp(-float(x))) for n, x in zip(names, logits)}


class LayaOV:
    """Laya's own Agent (tokenizing, prompt layout, calibration) with its DecisionModel swapped for an
    OpenVINO copy, so the encoder runs on the Intel iGPU. Converted once, cached as fp16 IR."""

    def __init__(self, subfolder="typed-decisions", device="GPU", cache_dir=os.path.expanduser("~/.cache/ai-linux/bench-models")):
        import glob
        import openvino as ov
        import torch
        import laya
        snap = glob.glob(os.path.expanduser(
            f"~/.cache/huggingface/hub/models--convaiinnovations--laya/snapshots/*/{subfolder}"))[0]
        self.name = f"laya-{subfolder}-ov-{device.lower()}"
        self.agent = laya.Agent(snap, compile=False, device="cpu")
        xml = os.path.join(cache_dir, f"laya-{subfolder}.xml")
        core = ov.Core()
        if not os.path.exists(xml):
            # batch 2 so tracing keeps the batch axis symbolic (Laya's export notes: batch 1 gets baked in)
            ids = torch.randint(5, 1000, (2, 24))
            mask = torch.ones(2, 24, dtype=torch.long)
            mask[1, 20:] = 0
            mpos = torch.tensor([[1, 2, 3], [1, 2, 3]])
            mmask = torch.tensor([[True, True, True], [True, True, False]])
            qtype = torch.tensor([0, 0])
            m = self.agent.model.float().eval()
            with torch.no_grad():
                ovm = ov.convert_model(m, example_input=(ids, mask, mpos, mmask, qtype))
            os.makedirs(cache_dir, exist_ok=True)
            ov.save_model(ovm, xml, compress_to_fp16=True)
        self.compiled = core.compile_model(core.read_model(xml), device)
        req = self.compiled.create_infer_request()

        class Stub(torch.nn.Module):
            def forward(self, input_ids, attention_mask, marker_pos, marker_mask, qtype, **_):
                r = req.infer([input_ids.numpy(), attention_mask.numpy(), marker_pos.numpy(),
                               marker_mask.numpy(), qtype.numpy()])
                return torch.from_numpy(r[0].copy()), torch.from_numpy(r[1].copy())

        self.agent.model = Stub()

    def scores(self, text, menu):
        return self.ask(text, menu, INSTR)

    def ask(self, text, options, instructions):
        q = {"q": {"type": "choice", "instructions": instructions, "criteria": dict(options)}}
        return _norm(dict(self.agent.predict(text, q)["answers"]["q"]["probabilities"]))
