# Small-model and router benchmark (2026-10-27)

What was tested, how, and what came out, so the temporary test folders could be deleted.
Everything here re-runs from this folder (`router/`), using the project env `AI_Linux` plus Ollama.

## Decision

| Role | Model | Where it runs | Why |
|---|---|---|---|
| Main brain (chat, tools, vision) | **Qwen3.5-4B** (Ollama `qwen3.5:4b`) | NVIDIA | best tool routing (87 %), usable confidence, sees images |
| Battery-mode router | **jiwo-0.8b** (`eljiwo/jiwo-0.8b`, Apache-2.0) as an 8-bit OpenVINO export | Intel iGPU | matches the 4B models on decisions, knows when it is unsure, NVIDIA stays asleep |
| Alternative brain | **Gemma 4 E4B** (Ollama `gemma4:e4b`) | NVIDIA | best on short decisions (97 %), fastest (cache reuse), vision 4/5; slightly weaker routing |
| Dropped | Laya, Qwen3.5-0.8B, nomic-embed, gte-reranker, qwen3:4b-instruct | | see results below |

Modes: **performance** (charger or desktop) runs everything on NVIDIA, no small model loaded.
**Battery** runs jiwo on the Intel iGPU; chat and anything jiwo is unsure of wakes NVIDIA for the 4B.

## How the router test works

- **Extensible by design.** Every router gets the tool menu `{name: description}` at request time and returns a
  probability per option. A new tool is one new menu entry, no retraining. Two menus: **M16** (the 16 current
  tools + `none`) and **M26** (+10 tools the app doesn't have yet, checked against GLaDOS and the other references:
  get_time, system_status, set_timer, describe_screen, switch_window, type_text, toggle_wireless, remember,
  get_weather, find_file). `router/tools.py`.
- **Test set** `data/eval.jsonl`, 771 requests: hand-written ASR-style commands (`router/synthetic.py`), the
  MASSIVE English test split (CC BY 4.0, `mteb/amazon_massive_intent`) and CLINC150 (CC BY 3.0,
  github.com/clinc/oos-eval `data_full.json`), incl. out-of-scope chat as `none`. 30 % dev / 70 % test, stratified.
  `router/build_data.py` rebuilds it (put the two raw files in `data/` first).
- **Metrics** (`router/report.py`): the confidence threshold is fitted on dev so wrong actions stay at or below
  2 %, then scored on test. *Handled* = share of real commands acted on correctly without the big model;
  *wrong actions* = acted on, but the wrong tool; *new* = accuracy on the 10 unseen tools.
- **Decision models only choose.** Free-text values (a search query, a URL) come from code, from option lists
  built at runtime (installed apps, settings pages) or from the generative 4B. Jev-style models never generate.

## Results: tool routing, 26-tool menu, test split (528 requests)

| Router | Device | Correct | Handled alone | Wrong actions | New tools | Median |
|---|---|---|---|---|---|---|
| Qwen3.5-4B | NVIDIA | 86.7 % | 73.3 % | 3.6 % | 83.7 % | 514 ms (230 ms on Ollama 0.40.11) |
| Gemma 4 E4B | NVIDIA | 81.4 % | 71.4 % | 3.6 % | 91.5 % | 61 ms |
| Qwen3.5-4B | Intel iGPU | same model | | | | ~4 s (see note 1) |
| **jiwo-0.8b, OpenVINO 8-bit, any length (final)** | **Intel iGPU** | **84.5 %** | **74.1 %** | **2.3 %** | **90.1 %** | **502 ms** |
| jiwo-0.8b, OpenVINO fp16, padded to 640 | Intel iGPU | 85.2 % | 74.1 % | 2.7 % | 90.1 % | 743 ms |
| jiwo-0.8b, PyTorch | CPU | 85.2 % | 74.1 % | 2.7 % | 90.1 % | 3,323 ms |
| qwen3:4b-instruct | Intel iGPU | 82.2 % | 0 % (note 2) | 15 % if trusted | 88.7 % | 651 ms |
| Laya typed-decisions, OpenVINO | Intel iGPU | 60.8 % | 30.5 % | 3.4 % | 73.0 % | 148 ms |
| Laya typed-decisions, PyTorch | CPU | 60.8 % | 30.5 % | 3.4 % | 73.0 % | 713 ms |
| nomic-embed-text | Intel iGPU | 38.6 % | 32.7 % | 1.3 % | 78.7 % | 106 ms |
| gte-reranker-modernbert int8 | CPU | 32.6 % | 19.5 % | 1.9 % | 67.4 % | 958 ms |
| Qwen3.5-0.8B (zero-shot) | NVIDIA | 31.2 % | 32.3 % | 3.8 % | 51.8 % | 314 ms |

16-tool menu: jiwo 8-bit 89.6 % / 62.4 % handled, 2.7 % wrong, 352 ms; Qwen3.5-4B 85.2 % / 72.8 % handled; Laya 74.2 % / 11.2 %; qwen3:4b-instruct 85.8 %.

1. Qwen3.5 is a hybrid (linear-attention) model: Ollama 0.30.8 can't reuse the cached tool list for it, so every
   request re-reads ~520 tokens (log: `cached n_tokens = 61`, then 516 tokens re-evaluated at 110-145 tok/s).
   The plain-attention qwen3:4b-instruct reuses the cache (~13 new tokens per request).
   **Ollama 0.40.11:** still re-reads ~510 tokens (it restores a checkpoint only at token 63), but prompt reading is
   faster: 230 ms per routing request on NVIDIA (was 514 ms). Not re-measured on the iGPU (battery mode uses jiwo).
   Gemma 4 E4B reuses the cache fully, hence 61 ms.
2. qwen3:4b-instruct reports ~100 % confidence on wrong answers too, so no threshold can filter its mistakes.
   It also has no vision. (`qwen3:4b` with thinking off answered letter "I" for everything: unusable this way.)

## Results: other decisions, all on the Intel iGPU (`router/tasks.py`, 177 hand-written requests)

| Model | Confirm yes/no | Pick app | Settings page | Meant for me? | Feedback mood | Risky shell cmd | Overall | Median |
|---|---|---|---|---|---|---|---|---|
| Gemma 4 E4B (on NVIDIA) | 97 % | 100 % | 100 % | 87 % | 100 % | 100 % | **97 %** | 76 ms |
| **jiwo-0.8b, 8-bit any length (final)** | 97 % | 100 % | 100 % | 77 % | 79 % | 90 % | **91 %** | 158 ms |
| jiwo-0.8b, fp16 padded to 448 | 97 % | 100 % | 100 % | 77 % | 79 % | 86 % | 90 % | 501 ms |
| qwen3:4b-instruct | 94 % | 100 % | 92 % | 73 % | 92 % | 93 % | 91 % | 497 ms |
| Qwen3.5-4B | 92 % | 97 % | 96 % | 50 % | 100 % | 100 % | 89 % | 1,187 ms |
| Laya | 83 % | 66 % | 54 % | 73 % | 71 % | 79 % | 72 % | 68 ms |
| Qwen3.5-0.8B (jiwo's untrained base) | 78 % | 72 % | 31 % | 53 % | 62 % | 93 % | 66 % | 495 ms |

Small sets (24-36 per task): a few points of difference is noise. Calibration error (ECE) 0.06-0.09 for all but Laya (0.16)
and Gemma (0.02, the best). Gemma ran on NVIDIA, the others on the Intel iGPU, so its latency isn't comparable.

## Results: vision (`router/vision_check.py`, NVIDIA)

Five questions about a screenshot of the app's Models page, scored by keywords. Qwen3.5-4B 5/5 (0.7 s per answer),
Gemma 4 E4B 4/5 (0.5 s). One screenshot only: enough to confirm both read screen text, not a ranking.

## Results: argument filling (`router/args_eval.py`, 44 cases, Ollama structured output)

Qwen3.5-4B 31/44 at 649 ms (NVIDIA); some misses are the strict string match (e.g. "call mom" label).
Qwen3.5-0.8B 35/44 at 1.6 s (Intel iGPU) but with risky errors ("volume to thirty" became 100 + mute).
Needs a dedicated pass before shipping. Note: Ollama `/api/chat` ignored `format` for qwen3.5 with think off
(0.30.8); `/api/generate` with a `system` field enforced it.

## Results: power and devices (`power_test.py`, battery current x voltage, baseline ~14.2 W)

| Small model on | Extra watts, idle loaded | One request / 10 s | Latency | Energy / request |
|---|---|---|---|---|
| Intel iGPU | +0 W | +0.2 W | 1.16 s | ~12 J |
| CPU | | +5.8 W | 3.4 s | ~30 J |
| NVIDIA | +17 W (dGPU kept awake) | +22.7 W | 0.46 s | ~13 J |

The NVIDIA cost is standby: the dGPU suspends ~13 s after its model unloads.

## Keeping NVIDIA asleep on the Intel path (verified per process: only `/dev/dri/renderD128` = 0000:00:02.0 open)

- Ollama: `OLLAMA_LLM_LIBRARY=vulkan CUDA_VISIBLE_DEVICES=-1 OLLAMA_VULKAN=1 OLLAMA_IGPU_ENABLE=1
  VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.json` (without `OLLAMA_LLM_LIBRARY` its GPU discovery wakes NVIDIA).
- OpenVINO GPU: `OCL_ICD_VENDORS=/etc/OpenCL/vendors/intel.icd` (`OCL_ICD_FILENAMES` does not restrict).
- clinfo, nvidia-smi and the GNOME Resources app wake the dGPU on their own.

## jiwo on the Intel iGPU: how it is converted (`router/jiwo_lite.py`)

- **Loader without the jiwo package:** `transformers` loads the backbone (`Qwen3_5TextModel`), a `Linear` loads
  `readout.safetensors`, and the prompt follows jiwo's `prompting.py` (Apache-2.0). Same answers as the jiwo
  package: 25/25, confidence identical.
- **Final export, `convert_dynamic()`:** Optimum Intel's own pipeline (`export_from_model`, task
  `text-generation`, the path behind Intel's published `OpenVINO/Qwen3.5-*-ov` models). jiwo's backbone goes in as
  a `Qwen3_5ForCausalLM` whose output head is jiwo's 255-code readout; Intel's Qwen3.5 patch turns the
  linear-attention recurrence into one OpenVINO loop op, so **any prompt length** works (no padding, no buckets).
  Then `nncf.compress_weights` (int8 asym, per channel). 37 s, 8 GB peak RAM (developer machine only).
  Needs `optimum-intel` 2.2.0 with **`transformers` 5.2.x** (its Qwen3.5 config is pinned to 5.2; with 5.5 the
  attention-mask code fails to trace) and the CPU build of `torchvision` (`--index-url .../whl/cpu`).
- **Result:** one 0.76 GB file (`jiwo-int8.xml/.bin`) vs the original on CPU: 764/771 same answer, confidence diff
  median 0.005, max 0.06. Loads in ~8 s (no long GPU compile), 3.1 GB peak RAM for the test process
  (includes PyTorch, which the app won't need). fp16 copy: 1.5 GB, 5.6 GB peak, same speed.
- **Shipped bundle (2026-10-27):** `~/.local/share/ai-linux/models/jiwo-0.8b-openvino-int8/` holds
  `openvino_model.xml/.bin` (the int8 export, renamed), jiwo's `tokenizer.json`, `decision_config.json`,
  `tokenizer_config.json`, `chat_template.jinja` and `LICENSE`. It is also the Hugging Face upload as is; sizes and
  SHA-256 are pinned in `src/ai_linux/downloads.py`. The app's runtime, `src/ai_linux/engine/jiwo.py`, needs only
  `openvino`, `tokenizers` and `numpy` (no PyTorch): 120/120 same answers as this bench loader, 773 MB RSS.
- **OpenVINO phones home:** `import openvino` sends Intel a Google Analytics event (`openvino_telemetry`,
  client id in `~/intel/`) from a forked process. Once that fork deadlocked and left a 4 GB process behind after
  the engine quit. The engine and these bench scripts now block the module before importing openvino (OpenVINO
  falls back to its own no-op stub); traced with `strace -f`: no forks left. Machine-wide opt-out for other tools:
  `opt_in_out --opt_out`.
- **Earlier attempt (superseded):** a static `[1, L]` graph via `torch.export` with the prompt padded to 448/640
  tokens. `torch.jit.trace` needed more than 9 GB because Qwen3.5's linear-attention loops unrolled; the padded
  graphs took ~2 min and ~6.5 GB to compile per machine and capped the menu at ~27 tools.
- **Sharing:** the exported files are a complete model (weights + network). Anyone runs them with `pip install
  openvino` on an Intel GPU or any x86 CPU, plus jiwo's `tokenizer.json` and the prompt code. Not on NVIDIA GPUs
  (OpenVINO has no NVIDIA backend); performance mode doesn't use jiwo. Apache-2.0 allows redistributing the
  converted model with the licence and a note of the change.

## The benchmark list (benchmarkheaven.com/jev-models, open weights, read 2026-10-27)

Best open models under 1B parameters by capability score: jiwo 0.8b 47.2 (intelligence 15.9), Laya
typed-decisions 44.2 (5.5), bge-reranker-v2-m3 44.1, lev-350m 43.3 (4.8), Jez-small 43.2, Certo v1 43.2,
Qwen3.5-0.8B Decision Model 42.6 (7.3), Gutsy 0.8B 42.2 (8.6), GTE reranker ModernBERT-base 41.7,
watt-flash-0.1 (140M) 41.6. jiwo leads on both scores; our own tests agree.

## Other things learned

- **Network:** PyPI, python.org, jsDelivr and GitHub release downloads crawled at 6-16 KB/s while YouTube was fast:
  the carrier's (Airtel, AS45609) route to the Fastly CDN. Not the machine (no proxy, MTU 1500, DNS fine).
  Cloudflare WARP (MASQUE) fixed it: PyPI 1.8 MB/s, GitHub 2.2 MB/s.
- Ollama logprobs return only the first token, hence the letter-label prompt (A-Z, a-z) for generative routers.
- gemma4:e4b needs Ollama 0.40+ (0.30.8 returned HTTP 412). Ollama upgraded to 0.40.11 (official tarball, checksum
  checked) in `~/.local`; 0.30.8 kept at `~/.local/share/ollama-0.30.8-backup` until the voice assistant runs on 0.40.
  Gemma: 60 s first load, 3 GB on the GPU.
- Never keep test models loaded with `keep_alive: -1` on the iGPU: its memory is system RAM.
