# TOP 10 — Projects to Focus On for the AI Linux Assistant (Wayland "Siri")

> Derived from four sources: my verified [CANDIDATES.md](CANDIDATES.md) (27 projects) + three external reports
> ([Res_Clau.md](Res_Clau.md), [Res_Gemi.md](Res_Gemi.md), [Res_Perplex.md](Res_Perplex.md)).
> The three reports **disagreed** on the foundation/executor. I ran a 6-agent adversarial verification of the
> contested claims against live primary sources (repos, source files, GitHub API) on **2026-06-13** and ranked by
> what survived — not by majority vote. Crux corrections are flagged inline as **[VERIFIED]**.
>
> **Project goal:** local-first, Wayland-native (Ubuntu 26.04 / GNOME) voice assistant — local STT → small local
> LLM → local TTS, that holds a conversation **and executes real desktop/system actions**.
> **Hardware ceiling:** RTX 3060 Mobile **6 GB VRAM**, 16 GB RAM, i7-12700H.

---

## The recommended build stack (the spine)

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  YOUR REPO (daemon/orchestrator)                                      │
   │                                                                       │
   │  RealtimeSTT (CPU, +wake word)  ──►  Qwen3.5-4B via Ollama (GPU)      │
   │         ▲                                   │                          │
   │         │                                   ▼                          │
   │  Supertonic-3 TTS (CPU)  ◄──  route: answer | call MCP tool | delegate │
   │                                             │                          │
   │   FORK GLaDOS for this loop  ───────────────┤                          │
   └─────────────────────────────────────────────┼──────────────────────────┘
                                                  │
              ┌───────────────────────────────────┼────────────────────────┐
              ▼                                   ▼                         ▼
   computer-use-linux (MCP)            EXECUTOR (gptme/OpenCode)    Agent Skills (SKILL.md)
   AT-SPI + portals + ydotool          shell/python/files/browser   procedure RAG
   = the Wayland "hands"               = the heavy "hands"          (Fabric/OpenClaw libs, AIChat RAG)
```

**One-line answer:** **Fork GLaDOS** for the voice loop, bolt **computer-use-linux** on as the Wayland hands,
delegate hard tasks to **gptme** (or OpenCode), feed it **Agent Skills** procedures, and mine **Newelle** for UX.

---

## Ranked Top 10

| # | Project | License | Role in YOUR build | Why this rank |
|---|---------|---------|--------------------|---------------|
| 1 | **GLaDOS** [`dnhkng/GLaDOS`](https://github.com/dnhkng/GLaDOS) | MIT | **Voice-loop FOUNDATION (fork it)** | Only candidate that is MIT + fits 6 GB + does real in-loop MCP tool-calling. 2 of 3 reports agree. |
| 2 | **computer-use-linux** [`agent-sh/computer-use-linux`](https://github.com/agent-sh/computer-use-linux) | MIT | **Wayland desktop-control layer (MCP)** | The hardest capability; nothing else provides it; validated on your exact GNOME Shell 50.1. |
| 3 | **gptme** [`gptme/gptme`](https://github.com/gptme/gptme) | MIT | **Primary delegated EXECUTOR** | Lightweight Python, shell/python/browser/MCP, Ollama, tolerant of small models. |
| 4 | **OpenCode** [`anomalyco/opencode`](https://github.com/anomalyco/opencode) | MIT | **Alternative/stronger EXECUTOR** | Gemini's "cloud funnel" smear is **FUD [VERIFIED]**; fully local-capable, robust. |
| 5 | **Newelle** [`qwersyk/Newelle`](https://github.com/qwersyk/Newelle) | GPL-3.0 | **UX + component REFERENCE** | Closest existing product; mine its wake-word + click-to-approve exec + GTK4 UX. |
| 6 | **RealtimeVoiceChat** [`KoljaB/RealtimeVoiceChat`](https://github.com/KoljaB/RealtimeVoiceChat) | MIT | **Voice-loop reference + your STT/TTS libs** | Reference impl of your exact STT→LLM→TTS picks; lift its turn-detection/barge-in. |
| 7 | **Goose** [`aaif-goose/goose`](https://github.com/aaif-goose/goose) | Apache-2.0 | **Heavyweight EXECUTOR (server-driven)** | `goosed` REST/WS API = clean delegation; but needs a strong/cloud model, not 4B **[VERIFIED]**. |
| 8 | **Fabric** [`danielmiessler/Fabric`](https://github.com/danielmiessler/Fabric) | MIT | **Procedure/Skills library substrate** | 240+ markdown patterns → instant SKILL.md seed for your hierarchical RAG. |
| 9 | **AIChat** [`sigoden/aichat`](https://github.com/sigoden/aichat) | Apache-2.0 | **Local hybrid-RAG engine + Ollama proxy** | Built-in vector+BM25 RAG, zero external DB — the retrieval layer for your Skills. |
| 10 | **OpenClaw** [`openclaw/openclaw`](https://github.com/openclaw/openclaw) | MIT | **Skills ECOSYSTEM reference (mine, don't run)** | Largest SKILL.md/ClawHub library; but no Linux voice, ≥8 GB-VRAM, 138+ CVEs — reference only. |

---

## Per-project detail

### 1 · GLaDOS — fork this for the voice loop  ⭐ FOUNDATION
- **[VERIFIED] The "Portal persona is hardwired" objection is false.** Persona and voice are pure config
  (`configs/glados_config.yaml` → `personality_preprompt`, `voice`); `llm_processor.py` builds the system prompt
  from config. A neutral re-skin is a config edit, not a fork-fight.
- **[VERIFIED] Real in-loop MCP tool-calling**, not aspirational: `llm_processor.py` sends a tools list to the
  Ollama/OpenAI endpoint and parses `tool_calls` (both OpenAI and Ollama formats); `tool_executor.py` dispatches
  `mcp.*` to a live `MCPManager` over **stdio, HTTP, and SSE** mid-conversation. This is how it will trigger your
  executor and computer-use-linux.
- **Fits 6 GB** (README: Rocket-3B ~6.35 GiB, Ollama recommended, STT/TTS as ONNX on CPU, no PyTorch). MIT, active (pushed 2026-06-08).
- **Two real gaps to budget for:** (a) **no wake word by design** (always-listening Silero VAD) — you add wake-word via RealtimeSTT; (b) swapping default Parakeet/Kokoro for RealtimeSTT/Supertonic needs adapter code (no published plugin base class).

### 2 · computer-use-linux — the Wayland "hands"  ⭐ CRITICAL LAYER
- The single capability **no other project on the list delivers**: typing/clicking/window-control on GNOME Wayland via AT-SPI2 + xdg-desktop-portals + ydotool, exposed as an **MCP server** your local model (and executor) call as tools.
- Researcher confirmed it **live on your machine class** (GNOME Shell 50.1, portals present, AT-SPI tree readable). Young (≈169★, single maintainer) — treat as the layer you adopt and contribute to.
- This is why every report's "desktop control gap" points here, and why X11-era approaches (Open Interpreter OS-mode, gptme's `computer` tool) are the wrong tool — see cuts below.

### 3 · gptme — primary delegated executor
- **[VERIFIED] Toolset is real and Wayland-agnostic:** shell, python, browser (Playwright), MCP, Ollama, local Kokoro TTS; healthy (release 2026-06-08).
- **[VERIFIED] Caveat — discount its `computer` tool:** it's X11-only (hardcoded `xdotool`/`xrandr`, `DISPLAY=:1`, disabled by default). It will **not** drive native Wayland. So gptme does the shell/file/code work and calls **computer-use-linux** for GUI control — don't use gptme's built-in desktop tool.
- Best fit when the executor must run on a **small local model**: lightweight Python, most tolerant of a 4B.

### 4 · OpenCode — strong MIT executor alternative
- **[VERIFIED] Gemini's disqualification is FUD.** MIT (confirmed LICENSE); **no telemetry in the shipped agent** (maintainer: spans "arent enabled… we dont send them anywhere"; PostHog code is in a maintainer-only stats script, never runs on your machine); Ollama/llama.cpp/LM-Studio are first-class with user-set `baseURL`; the "refused local-metrics PRs" were **self-closed by their own authors as duplicates**.
- For air-gapped use set `OPENCODE_DISABLE_MODELS_FETCH=1`, `share=disabled`, `autoupdate=false`. Swap in over gptme if you want a more robust agent harness and can run a stronger model.

### 5 · Newelle — UX + component reference (not the foundation)
- **[VERIFIED] Not Flatpak-only** (builds from source via Meson/GNOME Builder/nix, unconfined → no Flatseal needed for ydotool). So Gemini's sandbox objection is overstated.
- **[VERIFIED] But not the fork foundation:** the voice/wake-word loop is welded to the GTK window — no GApplication hold, no tray, no autostart; closing the window quits; headless mode covers only api/gui-api/telegram, **not** the voice loop. GLaDOS already is the always-on headless model.
- **Mine it for:** openWakeWord handler, click-to-approve command-exec UX (your safety gate), MCP + extension API, GTK4 polish. It's the closest existing product to your vision — study it, don't build on it.

### 6 · RealtimeVoiceChat — voice-loop reference + your actual STT/TTS libs
- This is the packaged reference of **your exact picks**: RealtimeSTT (your STT) + RealtimeTTS + Ollama, ~500 ms, with `turndetect.py` smart turn-taking and barge-in. Lift its turn-detection/interruption logic into the GLaDOS loop. KoljaB also authors RealtimeSTT/RealtimeTTS — your wake-word + streaming building blocks.

### 7 · Goose — heavyweight executor, server-driven delegation
- **[VERIFIED] Real, healthy, AAIF/Linux Foundation** (49 k★, v1.37.0 2026-06-03); Ollama supported; **headless + `goosed` REST/WebSocket/OpenAPI server** = the cleanest way for your daemon to delegate complex tasks.
- **[VERIFIED] But Perplexity's selling point is stale:** "Lead/Worker (big plans, 4B executes)" was **removed**; the replacement Planning Mode is documented only cloud-planner→cloud-executor, and Goose is tuned for frontier models (heavy tool-calling, 4096-ctx Ollama default flagged "too low"). **A 4B local executor in Goose is risky** — use it only if you drive it with a stronger/cloud model.

### 8 · Fabric — your Skills/procedure library seed
- Not an assistant — a library of 240+ curated markdown "patterns." Its philosophy (discrete task prompts, piped, version-controlled) **is** your hierarchical-RAG-of-procedures idea. Map each pattern → a SKILL.md entry to bootstrap your Agent Skills corpus instead of writing from scratch.

### 9 · AIChat — local retrieval engine for the Skills layer
- Single Rust binary with **built-in hybrid RAG** (vector + BM25, `reciprocal_rank_fusion`, no external Chroma/Qdrant) and an OpenAI-compatible server that can proxy your Ollama. Use it as the retrieval brick that picks the right SKILL.md for a task on CPU. (Slight staleness: last major push Feb 2026.)

### 10 · OpenClaw — Skills ecosystem reference only
- Largest SKILL.md ecosystem (ClawHub) — directly the format you're adopting, so it's a goldmine of example procedures.
- **Do not run it as the foundation:** no first-party Linux voice, official guidance wants ≥8–11 GB-VRAM local models, 138+ CVEs, Anthropic banned its subscription OAuth (2026-04-04). Harvest skills, leave the runtime.

---

## How the three reports reconciled (what was right vs wrong)

| Question | Claude (Res_Clau) | Gemini (Res_Gemi) | Perplexity (Res_Perplex) | **Verified verdict** |
|---|---|---|---|---|
| Frontend foundation | Newelle | GLaDOS | GLaDOS | **GLaDOS** — Newelle has no daemon/voice-headless mode |
| Executor | gptme | Open Interpreter | Goose | **gptme or OpenCode** for local; Goose if stronger model |
| Open Interpreter "computer-use" | — | claimed built-in | — | **Overstated** — it's Codex CLI rebranded; cua is an optional curl-installed QA helper, no Wayland |
| gptme desktop control | claimed strong | — | — | **X11-only**, useless on Wayland; use computer-use-linux instead |
| OpenCode sovereignty | legit | "cloud funnel/telemetry" | legit | **FUD** — MIT, telemetry off, fully local-capable |
| Goose 4B Lead/Worker | — | — | centerpiece | **Stale** — feature removed; 4B executor risky |

**Net:** all three agreed the architecture must split **brain (voice loop) from hands (executor)** and use **MCP** to
bridge Wayland — that consensus is correct and is the backbone of the stack above.

---

## Cut from the top 10 (and why)

- **Open Interpreter** — now OpenAI Codex CLI rebranded; sandboxed *coding* agent, no Wayland, no real computer-use. Keep only as a *reference* for bubblewrap/seccomp sandboxing of code execution.
- **CAAL** — elegant n8n-as-MCP security pattern, but targets smart-home/n8n, default 8B model, Docker-Compose stack. Borrow the "LLM never sees credentials" pattern; don't adopt.
- **OpenVoiceOS** — best offline wake-word/STT/TTS donor, but its LLM can't tool-call (intent-only). Mine for voice-pipeline patterns; wake word is already covered by RealtimeSTT.
- **AgenticSeek / QwenPaw / LocalAGI / Moltis** — strong agents but over-engineered or chat-channel/web-UI shaped; AgenticSeek recommends ≥14B (busts 6 GB).
- **Cline / Codex CLI / Aider** — IDE/coding-tuned; Aider is coding-only. Composable components at most.
- **ShellGPT / AI Shell / Seeva / azibom / Local-Voice / NyarchAssistant** — utilities, toys, dead, or redundant forks.
- Full reasoning for all 27 is in [CANDIDATES.md](CANDIDATES.md).

---

## Decision (locked 2026-06-13): fully local now, API-LLM-ready later

**Lane chosen: strictly local & open-source now**, built behind an executor interface so a stronger **API LLM can be
added later for complex tasks** without reworking the core. Everything in the runtime path stays under our control and
open source.

**The concrete v1 stack:**
- **Voice loop:** fork **GLaDOS** (MIT) → swap in **RealtimeSTT** (CPU, +wake word) and **Supertonic-3** (CPU); keep the GPU for the LLM.
- **Brain:** **Qwen3.5-4B** via **Ollama** (Q4, fully on the 6 GB GPU, flash-attn + q8_0 KV cache).
- **Hands (Wayland):** **computer-use-linux** as an MCP server (AT-SPI + portals + ydotool).
- **Hands (heavy):** **gptme** (local, Ollama) as the delegated executor — invoked via a thin `Executor` interface.
- **Knowledge:** **Agent Skills** (SKILL.md), seeded from **Fabric** patterns, retrieved via **AIChat** hybrid RAG.
- **Future:** the `Executor` interface gets a second implementation (Claude Code via `claude-agent-sdk`, or OpenCode/Goose with a strong model) for complex tasks — swap-in, not rewrite.

**First milestone:** fork GLaDOS, replace its STT/TTS with RealtimeSTT + Supertonic behind adapter modules, point its
LLM at local Qwen3.5-4B/Ollama, and prove the round-trip voice loop works on your hardware — before adding tools.
