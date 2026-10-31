# Small-model and router benchmark (2026-10-27)

What was tested, how, and what came out, so the temporary test folders could be deleted.
Everything here re-runs from this folder (`router/`), using the project env `AI_Linux` plus Ollama.

## Decision

| Role | Model | Where it runs | Why |
|---|---|---|---|
| Main brain (chat, tools, vision) | **Qwen3.5-4B** (Ollama `qwen3.5:4b`) | NVIDIA | best tool routing (87 %), usable confidence, sees images |
| Battery-mode router | **jiwo-0.8b** (`eljiwo/jiwo-0.8b`, Apache-2.0) as an 8-bit OpenVINO export | Intel iGPU | matches the 4B models on decisions, knows when it is unsure, NVIDIA stays asleep |
| Alternative brain | **Gemma 4 E4B** (Ollama `gemma4:e4b`) | NVIDIA | best on short decisions (97 %), fastest (cache reuse), vision 4/5; slightly weaker routing |
| Optional small model | **Laya** typed-decisions, OpenVINO fp16 (torch-free port) | Intel iGPU | 88 ms, but routes 60.8 % vs jiwo 84.5 %; never the default |
| Dropped | Qwen3.5-0.8B, nomic-embed, gte-reranker, qwen3:4b-instruct; Gemma 4 E2B not recommended | | see results below |

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
| Gemma 4 E2B (2026-10-28) | NVIDIA | 71.8 % | 6 % (note 2) | 0 % | 91.5 % | 35 ms |
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
2. qwen3:4b-instruct and Gemma 4 E2B report ~100 % confidence on wrong answers too, so no threshold can filter their mistakes.
   It also has no vision. (`qwen3:4b` with thinking off answered letter "I" for everything: unusable this way.)

## Results: other decisions, all on the Intel iGPU (`router/tasks.py`, 177 hand-written requests)

| Model | Confirm yes/no | Pick app | Settings page | Meant for me? | Feedback mood | Risky shell cmd | Overall | Median |
|---|---|---|---|---|---|---|---|---|
| Gemma 4 E4B (on NVIDIA) | 97 % | 100 % | 100 % | 87 % | 100 % | 100 % | **97 %** | 76 ms |
| Gemma 4 E2B (on NVIDIA, 2026-10-28) | 97 % | 97 % | 100 % | 70 % | 96 % | 100 % | 93 % | 44 ms |
| **jiwo-0.8b, 8-bit any length (final)** | 97 % | 100 % | 100 % | 77 % | 79 % | 90 % | **91 %** | 158 ms |
| jiwo-0.8b, fp16 padded to 448 | 97 % | 100 % | 100 % | 77 % | 79 % | 86 % | 90 % | 501 ms |
| qwen3:4b-instruct | 94 % | 100 % | 92 % | 73 % | 92 % | 93 % | 91 % | 497 ms |
| Qwen3.5-4B | 92 % | 97 % | 96 % | 50 % | 100 % | 100 % | 89 % | 1,187 ms |
| Laya | 83 % | 66 % | 54 % | 73 % | 71 % | 79 % | 72 % | 68 ms |
| Qwen3.5-0.8B (jiwo's untrained base) | 78 % | 72 % | 31 % | 53 % | 62 % | 93 % | 66 % | 495 ms |

Small sets (24-36 per task): a few points of difference is noise. Calibration error (ECE) 0.06-0.09 for all but Laya (0.16)
and Gemma (E4B 0.02, the best; E2B 0.06). Gemma ran on NVIDIA, the others on the Intel iGPU, so its latency isn't comparable.

## Results: vision (`router/vision_check.py`, NVIDIA)

Five questions about a screenshot of the app's Models page, scored by keywords. Qwen3.5-4B 5/5 (0.7 s per answer),
Gemma 4 E4B 4/5 (0.5 s). One screenshot only: enough to confirm both read screen text, not a ranking.
2026-10-28, a new Models-page screenshot (no Laya row, so question 5 is void): Qwen3.5-4B 4/4 (2.6 s, taller image),
Gemma 4 E2B 2/4 (0.2 s). E2B called the app "an AI application" and the loaded model "Ollama" (it was gemma4:e2b).

## Results: argument filling (`router/args_eval.py`, 44 cases, Ollama structured output)

Qwen3.5-4B 31/44 at 649 ms (NVIDIA); some misses are the strict string match (e.g. "call mom" label).
Qwen3.5-0.8B 35/44 at 1.6 s (Intel iGPU) but with risky errors ("volume to thirty" became 100 + mute).
Needs a dedicated pass before shipping. Note: Ollama `/api/chat` ignored `format` for qwen3.5 with think off
(0.30.8); `/api/generate` with a `system` field enforced it.

## Results: argument filling through the engine (`router/fill_eval.py`, 53 cases, 2026-10-27/28)

Runs `Brain.fill` on the shipped tools. `engine` = as shipped (the tool's code parser, then jiwo for a choice, then
the big model); `big` = the big model alone. Results land in `results/fill_*.json`.

| Path | Correct | Median |
|---|---|---|
| Shipped (`engine qwen3.5:4b`) | 52/53 (code parsers 43/44, instant) | |
| `big qwen3.5:4b` (run at 51 cases) | 50/51 | 515 ms |
| `big gemma4:e4b` (run at 51 cases) | 51/51 | 186 ms |
| `big gemma4:e2b` (2026-10-28) | 50/53 | 111 ms |

- The one shipped miss: "a third of the way up" read as "turn it up" (ambiguous, harmless).
- Fixes from this run: "half"/"quarter" now parse; jiwo can pick "exact" so a spoken exact level goes to the big model.
- E2B's 3 misses returned no arguments rather than wrong ones (maps search, uptime command, screen question).
- **E2B as the battery big model: not recommended (2026-10-28).** Fast and small, but it routes worse (71.8 %) than
  jiwo (84.5 %), the model it would back up, its confidence is unusable, and it misread 2 of 4 screenshot questions.
  Decisions 93 % and fill 50/53 are fine. Not adopted; the default is unchanged.

## Results: context window (2026-10-27)

- A full turn (21 tools, 8 history turns, 1500-char "about you") is ~2.5K tokens (qwen 2474, gemma 2065); a turn with a
  1080p screenshot: qwen 2519, gemma 1398. **8K context is plenty.**
- GPU memory from the driver: qwen3.5:4b 4.0 GB at 8K, 4.2 GB at 16K; gemma4:e4b 4.75 / 4.9 GB of 6; gemma4:e2b 3.26 GB at 8K (3.11 at 4K).
  Ollama `/api/ps` misreports Gemma's GPU memory (0.35 GB for e4b, 476 MB for e2b): don't trust it for Gemma.

## Results: speech on the CPU (2026-10-27)

STT (Parakeet), TTS (SuperTonic) and VAD run on the CPU (onnxruntime CPU provider); jiwo on the Intel iGPU; the big
model on NVIDIA. ONNX Runtime's default (all 20 threads, busy-wait spinning) burned ~14 s of CPU per utterance. Fix in
`src/ai_linux/engine/onnx_opts.py`: `session.intra_op.allow_spinning=0`, 4 threads for STT, 2 for TTS
(`AI_LINUX_CPU_THREADS` overrides).

| | Before | After |
|---|---|---|
| Parakeet TDT, 4.5 s sentence | 0.79 s, 14.5 s CPU | 0.26 s, 0.94 s CPU |
| Idle CPU | ~14 s per utterance | 0 |
| TTS, short reply | 0.81 s, 15 s CPU | 0.40 s, 0.8 s CPU |

Parakeet CTC: 51 ms. Wake word "Phoenix" (the default name): 36/36 with both STT engines. "commuter" and "compute"
falsely woke "computer"; nothing tested falsely woke "Phoenix".

## Laya, EmbeddingGemma 2 (2026-10-28)

- **Laya** routed 60.8 % vs jiwo 84.5 %, so it is only an optional small model (catalog id `laya`, never the
  default). Its pip package (0.4.15) imports torch and transformers even in `onnx_agent`, so `engine/laya.py` is a
  torch-free port of the choice path of Laya's `common.py` (sequence layout, temperature buckets and clamp; Apache-2.0,
  attributed). `router/laya_export.py` exports the whole `DecisionModel` to OpenVINO fp16 (traced at batch 2 so the
  batch axis stays dynamic; inputs `input_ids`, `attention_mask`, `marker_pos`, `marker_mask`, `qtype`), fills
  `~/.local/share/ai-linux/models/laya-typed-decisions-openvino/` (model card + LICENSE included) and checks the port
  against Laya's own torch `Agent` on 297 requests (2026-10-28):

  | | Port, CPU | Port, Intel iGPU |
  |---|---|---|
  | Same token ids and option positions | 297 / 297 | 297 / 297 |
  | Same top answer | 297 / 297 | 297 / 297 |
  | Probability difference | median 0.0001, max 0.0018 | median 0.0019, max 0.026 |
  | Median time | 344 ms | 88 ms |

  25 of those cases are the golden fixture `tests/fixtures/laya_golden.json` (`tests/test_laya.py`, skipped without
  OpenVINO). Source pinned to `convaiinnovations/laya` revision `7b928d8`, subfolder `typed-decisions`. The export
  needs about 6 GB: the GPU compile ran out of memory with torch's copy still loaded, so the script frees it first and
  caches the torch results in `~/.cache/ai-linux/laya_ref_probs.json`. **Not uploaded yet:** the target repo is
  `itsDigvijaysing/laya-typed-decisions-openvino`; after the upload, set `downloads.LAYA_REVISION` to its commit.
- **EmbeddingGemma 2** (`google/embeddinggemma-2`, 744M, text/image/audio/video embeddings, Apache-2.0): pulled into
  Ollama, not tested. No slot today: jiwo routes, and embedding-based routing was dropped earlier for misfiring. Could
  serve a later history/memory search.
- jiwo is published at `itsDigvijaysing/jiwo-0.8b-openvino-int8`, pinned to revision `801c88f`. OpenVINO's telemetry
  is blocked in-process (`sys.modules["openvino_telemetry"] = None`).

## Assistant name (`router/name_check.py`, 2026-10-28)

SuperTonic read the name test's 5 sentences in 4 voices (M1, F1, M3, F3); Parakeet TDT transcribed them; the app's
wake matcher (`ai_linux.wake`) scored them. False wakes: the 771 ordinary commands in `data/eval.jsonl` (none contains
the name) that the matcher would still take as the name. A synthetic voice is clearer than a person, so this ranks
names; the app's own name test (Voice page) checks the user's real voice.

| Name | Heard | False wakes | Notes |
|---|---|---|---|
| **Phoenix** (default) | 20/20 | 0 | |
| Pegasus, Griffin, Sphinx, Zephyr, Dragon, Nova, Jarvis | 20/20 | 0 | Dragon and Nova are common words in talk and TV |
| Orion, Atlas, Apollo, Odin, Titan | 19/20 | 0 | "Aurea", "at", "Paulo", "Loaden", "Tighten" |
| Computer | 20/20 | 2 | a word people use in commands |
| Hermes | 20/20 | 2 | "where's" reads as Hermes |
| Kraken | 11/20 | 0 | heard as "cracking", "crack and" |

The split-name rule ("Dora Emon" for Doraemon) first used the normal 0.75 similarity and gave Hermes 5 false wakes
("where is"); joined words now need 0.85.

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
  `readout.safetensors`, and the prompt follows jiwo's `prompting.py` (MIT). Same answers as the jiwo
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

## Speech recognition: int8, the Intel GPU, live text (`asr/`, 2026-10-28)

Test set (`asr_bench.py make`): 12 commands, SuperTonic voices M1 and F1, four conditions (clean, white noise at 10 dB
and 0 dB SNR, a second voice 10 dB below the speaker): 96 clips of 3.5-6 s. "30%" for "thirty percent" counts as right.
Our Parakeet is **FP16** (609 M weights at 2 bytes, encoder 1.24 GB), not FP32.

| | Now: FP16, CPU, 4 threads | int8, CPU | FP16, Intel GPU (OpenVINO 2026.4) |
|---|---|---|---|
| Word errors: clean / noise 10 dB / noise 0 dB | 0 / 0 / 2.3 % | 0 / 0.5 / 1.8 % | 0 / 0 / 2.3 % |
| Same text as now | | 79 / 96 (mostly capitals, commas) | 96 / 96 |
| 5 s sentence | 0.24 s, 0.87 s CPU | 0.12 s, 0.39 s CPU | 0.09 s, 0.14 s CPU |
| Memory after load | 4.2 GB | 1.6 GB | 0.7 GB + 1.4 GB GPU buffers (shared RAM) |
| Load | 4.8 s | 2.0 s | 9.9 s the first time, then 0.7-1.7 s from cache |

All three keep NVIDIA asleep (the GPU run opens only `renderD128` = 0000:00:02.0). With a second voice underneath,
all three score 41-49 % because Parakeet also writes down the background words.

- **int8:** ONNX Runtime's `quantize_dynamic` needs FP32 input and ran out of memory at the 6 GB cap; `quant_lite.py`
  writes the same nodes one weight at a time (217 MatMul weights to int8, 76 M convolution weights left FP32: 879 MB).
  OpenWhispr's sherpa-onnx build (622 MB) also shrinks the convolutions. int8 compiled for the GPU: killed at 6 GB.
- **GPU, new lengths:** each audio length the GPU hasn't seen costs ~0.6 s once (kernels are compiled per shape and
  kept in the cache folder; the table's 0.09 s is for cached shapes). Padding the encoder input to whole seconds (the
  true length passed, output trimmed) caps it at 15 shapes for the 15 s limit: 94 / 96 transcripts identical, the 2
  changes in 0 dB noise and background speech. The cache is 1.3 GB on disk (a copy of the weights);
  `CACHE_MODE=OPTIMIZE_SIZE` doesn't apply to ONNX input.
- **First decode after load:** 1.4-1.6 s on CPU and GPU, then 0.1-0.25 s. The engine warms the big model, not Parakeet.
- **Live text** (`live_sim.py`, the growing utterance decoded every 0.6 s): each update 0.07-0.15 s and 0.14-0.33 s CPU
  on the GPU, 0.1-0.35 s and 0.26-1.28 s CPU on the CPU (about 2 s vs 5.5 s of CPU for a 5 s command). Updates reach
  the final words 0.5-1 s before the sentence ends; the first ones can be wrong ("Right a remote." for "Write a
  reply").

## Echo cancelling: PipeWire's module against the engine's own AEC3 (2026-10-31)

Scripts: `bench/echo/` (`make_clips.py` then `run_cfg.py`, which plays sound and loads the old module for its run;
`replay.py` is silent, over the recordings). Why: Phoenix kept failing to hear, or heard itself, mostly on battery. One
test per row, 33 s: a reply through the sink
under test, then a second voice played straight to the speaker as "the user" with nothing else playing, then both. On
the charger, same volume, the laptop otherwise idle. "Voice" = Silero >= 0.5 on the second voice alone. "Resyncs" =
PipeWire's `follower ... resync` lines for the USB card, each one silence written into the playback.

| Setup | Profile | Resyncs | Voice, plain mic | Voice, through the chain |
|---|---|---|---|---|
| Module, quantum 480 (what shipped) | power saver | 291, 285 | 62, 58 % | **19, 26 %** |
| Same, noise suppression off | power saver | 291 | 68 % | 17 % |
| Same, mono 16 kHz / mono 48 kHz | power saver | 285, 284 | 66, 53 % | 51, 51 % |
| Module, quantum 960 / 1920, block matched | power saver | 142, 75 | 49, 69 % | **0, 0 %** |
| Module, quantum 480 | balanced | 0 | 64 % | 63 % |
| Module, quantum 480 | performance | 0 | 69 % | 68 % |
| Module, quantum 480, 20 busy processes | power saver | 0 | 65 % | 61 % |
| No module, quantum left alone | power saver | 0 (5 runs) | 60-68 % | same frames (raw) |

- **So the power state decides, and a busy CPU rescues it.** The trigger is the module's sink being idle: resyncs ran at
  ~33 a second between replies and stopped while a reply played. The journal agrees: `front:1p ... resync` 0 before
  the chain came in on 28 October, then about 21k, 74k, 66k and 33k a day (28 to 31 October). Why low clocks tip the module over isn't traced;
  mono 16 kHz resynced just as much, so the cost of AEC3 itself isn't it.
- **Battery itself wasn't measured:** power saver stands in for it. power-profiles-daemon 0.30 is battery-aware, and
  on the charger `balanced` set EPP `balance_performance`; what it sets on battery wasn't read.
- **This test didn't reproduce the leak from real use:** with only a reply playing the module leaves -37 to -60 dB and
  0-3 % voice, even in power saver. The logs do show our words becoming requests through the barge-in path, which
  skipped the word check. How much the module added to that in real use stays unproven.
- **The engine's AEC3** (`echo.py` replayed over the no-module recordings, no module in the graph): nothing playing,
  the frames are identical to the mic; 9 replies alone: -40 to -42 dB, 0 % voice, no false barge-in, and Parakeet got
  no words from the 5 of them it was given. A real person talked over the first reply of one long run by accident:
  Parakeet got their whole sentence from the cleaned audio. One reply (of 13) leaked: -30 dB, "The weather in your city
  from the day ...", and `own_words` catches that. Replayed offline.
- **Live, once, through the real `PipeWireAudioIO` and reader thread** (30 s, power saver, on the charger; the engine and
  app were not running): 943 of 946 frames, 0 resyncs, the graph left at quantum 1024, `clean()` median 0.87 ms and worst
  21 ms of a 32 ms frame. A voice alone: same level as the mic, its whole sentence transcribed. Two replies alone:
  -40.6 and -40.9 dB, 0 % voice. A voice over a reply: it paused 1.4 s after they started, and Parakeet got "remind me
  to call the bank before ..." with the last words wrong. **The reply right after that double talk leaked:** -28.5 dB
  and Parakeet read nearly all of it, yet Silero stayed at 0 %, so no recording would have started. Silero misses
  cancelled speech that Parakeet can still read, which is why a pause is slow and its transcript still good.
- **Talking over a reply is its weak side.** A voice mixed over a reply: pauses it 1.4-1.6 s after they start at equal
  level, 1.2-1.4 s at +6 dB, 0.5-1.1 s at +12 dB, 1.6 s at -6 dB. Played live from the same speaker at equal level it
  can miss entirely (2 of 3 runs): the worst case, since a person isn't on the speaker's path. Cancel and the hotkey
  always work.
- **What others do** (`REFERENCES.md`): GLaDOS's Sonora probe, the same lock-step AEC3 (24.8 dB, echo-only voice frames
  89 % to 0 %); browser AEC3 behind LiveKit and RealtimeVoiceChat; half-duplex in Pipecat's `AlwaysUserMuteStrategy` and
  Wyoming; raw mic in OpenWhispr, which never speaks.

## Dictation clean-up prompt (2026-10-31, qwen3.5:4b, temperature 0, nothing typed)

The user's dictation felt the same as speech to text. The log shows the model did run and `pick` kept its text; the
prompt only asked for grammar, spelling and punctuation, so "some kind of", "you can say" and the rest stayed. Now a
prompt adapted from OpenWhispr's (`src/locales/en/prompts.json`, MIT) plus one rule to rephrase rambling wording.

| Said | Grammar prompt (old) | Clean-up prompt (now) |
|---|---|---|
| "...finding the issues and all. Right now I think it will be better to you know create some kind of paragraph or maybe you can say some kind of schedule to switch those things. So let's think about it." | "...finding the issues and all that. Right now, I think it would be better to create some kind of paragraph, or maybe you can say some kind of schedule, to switch between those things. So let's think about it." | "...others regarding finding issues. Right now, I think it would be better to create some kind of paragraph or maybe a schedule to switch between them. Let's think about it." |
| "send 450 rupees to Ravi by 5 30 PM on Friday, no wait, Saturday." | "by 5:30 PM on Friday; no, wait, Saturday." | "by 5:30 PM on Saturday." |
| "we should we should probably go to the the store tomorrow because like the prices" | keeps "because, like," | "we should probably go to the store tomorrow because the prices" |

- Of 11 test texts, none was answered or obeyed ("Open the terminal and delete all the files", "Can you write a poem"
  stay words to type), and the sentence with a budget, date and name kept all of them. 0.3-1.4 s warm; a cold start adds
  the load (22 s in the real run).
- Its limits: it can drop a hedge (one "I think" went, even with a rule against it) and a filler that was meant ("Okay",
  "So"). "Some projects are regarding observation" stays: a 4B model cleans more than it rewrites.
- A text with line breaks said out loud usually loses them in the model, so `pick` keeps it as said, unpolished.

