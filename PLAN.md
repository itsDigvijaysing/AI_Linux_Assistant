# AI Linux Assistant — Master Plan

> Single source of truth for this project. Supersedes the old README / FILE_INDEX /
> IMPLEMENTATION_SUMMARY / MIGRATION_GUIDE / QUICKSTART / CHECKLIST (removed).
> Research rationale is preserved in [TOP10.md](TOP10.md) and [MD/](MD/).
>
> **Status:** Planning locked · **Date:** 2026-06-15 · **Owner:** king

---

## 0. Hard constraints (do not violate)

- **Never commit.** No `git commit`, no staging. All work stays in the working tree until the owner decides otherwise.
- **Fully local & open-source** in the runtime path. No cloud LLM in v1 (an API LLM can be added later behind an interface — Phase 7, not now).
- **Hardware ceiling:** RTX 3060 Mobile **6 GB VRAM**, 16 GB RAM, i7-12700H, Ubuntu 26.04 / GNOME / **Wayland**.
- **Simple but trackable.** v1 stays small; "beyond simplicity" is a deliberate, staged growth path — never a big-bang.
- **Evolve THIS repo** as the base (not a GLaDOS fork). Clone good repos as references first, then port code/patterns in — no blind copy-paste-and-delete.

---

## 1. Vision

A fully local, Wayland-native voice assistant you can **talk to naturally** and that can **execute simple/regular desktop tasks** as you talk. v1 is intentionally small. But it is **built with seams** (a tool/executor interface, a retrieval interface) so it can grow "beyond simplicity" — delegated execution of harder tasks, a memory/skills layer, and optionally a stronger brain — **without reworking the core.**

**v1 target:** hands-free listening **and** task execution, reached through 4 trackable phases.
**Growth path:** Phases 5–7 add the powerful pieces, each plugging into a v1 seam.

---

## 2. Decisions locked

| Topic | Decision |
|---|---|
| Foundation | **Evolve this repo**; mirror GLaDOS's architecture, port code where it helps |
| Build method | **Clone references first → study/verify → port patterns in**; never blind copy-paste |
| Commits | **Never commit** (this doc + cloned repos stay untracked / uncommitted) |
| Voice loop | Always-on (VAD), streaming STT/TTS on **CPU**, LLM on **GPU** |
| Brain | Local LLM via **Ollama** with tool-calling. Default **`qwen3:4b`** (verified tool-calling, fits 6 GB @ Q4); lighter **`qwen3:1.7b`** via Settings panel / `GLADOS_LLM_MODEL` |
| Hands (Wayland) | **computer-use-linux** run as an **MCP server** + a few safe shell/file tools |
| Safety | **Confirm-before-execute** gate on every action (Newelle's click-to-approve pattern) |
| UX reference | **Newelle** (wake-word handler, approval UX, GTK4 polish) |
| Executor (heavy tasks) | **Staged → Phase 5.** gptme first (light, local-friendly); **OpenCode** as stronger alt (verify its repo/stars first); behind the v1 Executor interface |
| Memory / skills | **Staged → Phase 6.** Seed from **Fabric** patterns + **OpenClaw**-harvested skills; small RAG growing toward **AIChat**'s hybrid RAG only if needed |
| Stronger brain | **Staged → Phase 7.** Optional API/cloud LLM behind the same Executor interface; local stays default |
| Goose | **Kept in roadmap as conditional** heavyweight executor — only with a stronger/cloud model (Phase 5/7) |
| OpenClaw | **Harvest skills only, never run** (CVEs, ≥8 GB models, no Linux voice) |
| Set aside | **PersonaPlex** (7B full-duplex fights the 6 GB ceiling and the modular tool-calling design) |

### Answers to the open questions (for the record)
- **gptme vs OpenCode (170k★):** popularity ≠ fit. For a 4 B local model driven by a daemon, gptme's minimalism wins; OpenCode is tuned for stronger models. Confirm OpenCode's actual repo/stars before weighting them. Both land at **Phase 5** behind the same interface — decide then.
- **RealtimeVoiceChat / LiveKit:** reference only (turn-detection/barge-in). LiveKit is networked WebRTC transport — overkill for a local single machine.
- **Goose:** not dropped — kept as a conditional Phase-5 option requiring a stronger model.
- **AIChat:** too big to adopt whole; start with a tiny retriever, grow into its RAG only if the tiny one isn't enough (Phase 6).

---

## 2b. Verification log — Phase 1 recon (2026-06-15)

All candidate repos checked against the live GitHub API + shallow clones in untracked `refs/`. **All 10 exist.** Corrections vs TOP10 and the copy policy:

| Repo | License (verified) | Lang | Copy policy |
|---|---|---|---|
| dnhkng/GLaDOS | MIT | Python | **OK to copy/vendor** — the only repo we may legally reuse source from |
| agent-sh/computer-use-linux | MIT | Rust | **run as MCP server** (don't port source) |
| KoljaB/RealtimeVoiceChat | **none** | Python | patterns only — reimplement (no license) |
| qwersyk/Newelle | GPL-3.0 | Python | patterns only — reimplement (copyleft is viral) |
| danielmiessler/fabric | MIT | Go | mine markdown patterns |
| sigoden/aichat | Apache-2.0 | Rust | skim patterns / optional service |
| openclaw/openclaw | NOASSERTION | TS | harvest skill *concepts*; never run/copy |
| anomalyco/opencode | MIT | TS | Phase 5 — real, ~174k★ (TOP10 handle was correct) |
| gptme/gptme | MIT | Python | Phase 5 |
| aaif-goose/goose | Apache-2.0 | Rust | Phase 5 (conditional) |

**Copy rule:** reuse *source* only from GLaDOS (MIT). Everything else = reimplement the idea in our own code, or run as a separate service.

**Capabilities confirmed by reading the clones:**
- **GLaDOS** already implements ~the whole v1: always-on loop, **MCP tool-calling** (`mcp/manager.py` + 7 built-in MCP servers + `tools/` dispatch + HTTP/stdio transport tests), **config-driven** persona/voice/model (defaults to **llama3.2**), **ONNX Parakeet ASR + Kokoro TTS on CPU**, plus a TUI and text-input mode. MIT.
- **computer-use-linux** — Rust MCP server (`rmcp`): AT-SPI + multi-compositor windows (GNOME/KWin/Hyprland/i3/COSMIC) + portal screenshots + ydotool; `doctor` readiness check; MCP safety annotations; extracted from Codex Desktop Linux.
- **RealtimeVoiceChat** `code/turndetect.py` (barge-in) and **Newelle** `src/utility/command_permissions.py` (approve-before-exec) confirmed as references.

**Concrete STT/TTS picks now have a working reference:** Parakeet (ASR) + Kokoro (TTS) as ONNX on CPU — exactly the "STT/TTS on CPU, LLM on GPU" budget split.

**Open implication:** GLaDOS is so complete (and MIT) that "evolve this repo" realistically means *vendoring GLaDOS's modules in*, which is close to a fork. Flagged for re-confirm (see checkpoint).

## 2c. Vendoring plan — GLaDOS → this repo (decided 2026-06-15)

GLaDOS is the engine. We copy its MIT source in and own it here (manual upstream re-pull).

**GLaDOS facts:** package `glados`, **Python ≥3.12** (machine has 3.13 ✓), runner **uv**, entry `glados = glados.cli:main`. Core deps: sounddevice, **mcp≥1.25**, pydantic, httpx, loguru, textual, numba, soundfile, onnxruntime(-gpu). Stack: Silero VAD + Parakeet ASR (ONNX) + Kokoro TTS, Ollama LLM (default **llama3.2**). Modes: `glados` (voice), `glados tui`, `glados start --input-mode both`.

**Copy now (whole package; trim later):**
- `refs/GLaDOS/src/glados` → `src/glados/` (keep package name → no import rewrites)
- `refs/GLaDOS/configs` → `configs/` · `refs/GLaDOS/models` (configs only, weights download at runtime) → `models/`
- adopt `refs/GLaDOS/pyproject.toml` (uv + deps; rename project later) · copy `LICENSE.txt` → `LICENSE.GLaDOS` (**MIT attribution — required**)

**Trim AFTER it runs (YAGNI):** `vision/` (+opencv/FastVLM), `api/` (litestar). Keep `autonomy/` (the self-driving behaviour), `glados_ui/` (TUI), `core/`, `mcp/`, `tools/`, `ASR/`, `TTS/`, `audio_io/`.

**Then (Phase 2/3):** neutral persona in `configs/`; LLM already = local llama3.2; add **computer-use-linux** as an MCP server entry (MCPManager handles stdio/HTTP/SSE); add **Newelle-style approve-before-exec** gate; reference **RealtimeVoiceChat** `code/turndetect.py` for barge-in.

**Retire after the engine imports:** old `main.py`, `app_frontend.py`, `personaplex_pipeline.py`, `app.py`, `assistant.py`, `tts.py`, `tts_improved.py`, `utils.py`, `config.yaml`, `setup.py`, `poetry.lock`, `run.sh`, `setup_env.sh` (superseded by the GLaDOS engine + uv).

## 2d. Phase 1 status — DONE (2026-06-15)

- Repos verified + cloned to `refs/` (gitignored). GLaDOS + computer-use-linux capabilities confirmed.
- GLaDOS engine vendored to `src/glados/`; lazy-vision edit (`vision/__init__.py`) drops the hard opencv dep.
- conda env **AI_Linux** (Python 3.12.13); lean install via `pip install -e ".[cpu]"`:
  onnxruntime 1.26, numba 0.65, mcp 1.27, sounddevice 0.5.5, soundfile, textual 8.2, numpy 2.4, pydantic, httpx, loguru (+ portaudio via conda). **No opencv / litestar / nemo.**
- **Build cross-check PASSED:** 13/13 core modules import (engine, cli, mcp.manager, ASR×2, TTS×2, vad, audio_io, tools, autonomy.loop, tool_executor); `cv2` not loaded.
- **Not run yet** (per instruction — run later).

### To run later (when ready)
1. Ollama up + model:  `ollama serve` &  `ollama pull qwen3:4b`  (or lighter `qwen3:1.7b`)
2. `conda activate AI_Linux`
3. First run downloads ONNX weights (Parakeet ASR + Kokoro TTS + Silero VAD):
   - `glados tui`                      # text UI — lightest first smoke test
   - `glados`                          # voice mode
   - `glados start --input-mode both`  # voice + text
4. Config in `configs/` (`assistant_config.yaml` = neutral persona, llama3.2).

### Next (Phase 2/3 — not started)
- Neutral persona/voice in `configs/`; confirm llama3.2 target.
- Add **computer-use-linux** as an MCP server entry (build the Rust binary; register in config — MCPManager handles stdio/HTTP/SSE).
- Newelle-style approve-before-exec gate; RealtimeVoiceChat `turndetect` for barge-in.
- Retire superseded legacy files (awaiting your OK).

## 2e. Phases 2–4 status — code complete, run later (2026-06-15)

- **Phase 2 (voice loop):** `configs/ai_linux_config.yaml` — neutral persona, llama3.2 via Ollama, Kokoro `af_bella`, CPU `ctc` ASR, `input_mode: both`, barge-in on, built-in info MCP tools. Validated by GladosConfig. Commit `e17ab56`.
- **Phase 3 (hands + safety):** `core/tool_safety.py` confirm-before-execute gate, hooked into `tool_executor.run()` (auto-deny in autonomy, y/N otherwise, fail-safe deny, graceful LLM rejection). computer-use-linux MCP entry added (commented — enable after installing the binary). Gate unit-tested. Commit `02a4f24`.
- **Phase 4 (polish + skills):** barge-in already implemented (`interruptible`), wake-word supported (`wake_word`, currently always-listening); `skills/` procedure seed added (retrieval wiring is Phase 6).
- **Still requires your live run** (deferred per instruction): first `glados` run downloads ONNX weights; needs Ollama + `qwen3:4b`; install the computer-use-linux binary to enable desktop control.

## 2f. Phases 5–7 status — DONE, lean/efficient build (2026-06-15)

Decision driver: efficiency + the stated goal (simple/regular tasks, **not** heavy agentic work).

- **Phase 5 — executor:** a lean **gated shell executor** `mcp/shell_server.py` (`mcp.shell.run_command`), confirm-gated exactly like computer_use. Chosen **over installing gptme/OpenCode/Goose** — those are heavyweight agent frameworks for complex work the goal excludes (Node runtime / strong-model assumptions / large dep surface). They remain documented drop-ins behind the same MCP/Executor interface for later. Commit `55abaf1`.
- **Phase 6 — memory & skills:** a tiny keyword **skills retriever** `mcp/skills_server.py` (`list_skills`, `find_skill`) over `skills/`, alongside GLaDOS's existing `memory` server. **No AIChat/Fabric install** (over-engineering); can grow into hybrid RAG behind the same interface. Commit `c2578a0`.
- **Phase 7 — stronger brain:** **no new code/deps** — it's a config swap (`completion_url`/`api_key`/`llm_headers`). Local `qwen3:4b` stays default; cloud/OpenAI-compatible example documented in `configs/ai_linux_config.yaml`.

**Active MCP servers:** system_info, time_info, memory, **shell**, **skills**, computer_use. Confirm-gated: `mcp.shell.*` + `mcp.computer_use.*`.

## 2g. Review hardening (2026-06-15)

Adversarial multi-agent judge pass (16 confirmed / 7 rejected; ~10 confirmed were one root cause). Fixes:
- **Safety gate redesigned** (`core/tool_safety.py`): the old blocking `input()` hung under the Textual `tui` (Textual owns the terminal) and contended with the text listener on stdin in `input_mode: both`. Now **non-blocking & fail-safe**: gated tools (`mcp.shell.*`, `mcp.computer_use.*`) are **denied unless the session is armed** (`GLADOS_ALLOW_ACTIONS=1`); **autonomy is hard-floor denied** independent of `GLADOS_CONFIRM_TOOLS` (closes the footgun where `""` silently disarmed autonomy). A `prompt_fn` hook remains for a future per-action TUI/voice confirmation.
- **Launcher `run.sh`** + `cli.py` `DEFAULT_CONFIG` now resolve to `configs/ai_linux_config.yaml` — previously a bare `glados start` loaded stock GLaDOS *without* our shell/skills/computer_use servers.
- **`.gitignore`** ignores downloaded weights (`*.onnx`/`*.bin`/`*.nemo`/`*.gguf`/…) so first run won't stage them.
- Polish: `vision/__init__` `__all__` no longer triggers cv2 on star-import; `shell_server` timeout collapsed to one 20s value under the 30s MCP cap; skills README corrected (skills ARE wired).

Rejected (correctly, no change): `shell=True` is the tool's intended purpose (gated); output/timeout caps adequate; keeping the vendored GLaDOS superset is fine; vision/api trim is correctly-sequenced future work; `disk_info` omission is intentional curation.

**Run:** `./run.sh` (voice+text) · `./run.sh tui` · `./run.sh download` · add `--allow-actions` to arm shell/desktop actions.

## 2h. Dual brain — local + Groq (2026-06-15)

The brain is swappable with **no code rewrite** — the engine auto-detects Ollama vs OpenAI-compatible endpoints:
- **Local (default):** `configs/ai_linux_config.yaml` — `qwen3:4b` via Ollama (GPU).
- **Groq (opt-in):** `configs/ai_linux_groq.yaml` — `llama-3.3-70b-versatile` via Groq's OpenAI-compatible API; run `./run.sh --groq`.

Speech (Parakeet/Kokoro on CPU), MCP tools, and the safety gate are identical across both — only the LLM is
remote. The Groq key comes from `GROQ_API_KEY` (engine env fallback generalized to
`GLADOS_API_KEY`/`GROQ_API_KEY`/`MINIMAX_API_KEY`) and is **never stored in a config**. Local stays the focus.

## 2i. v1 COMPLETE + committed + security-hardened (2026-06-27)

Everything below is implemented, verified, and **committed** — HEAD `4574bf2` on `main`, working tree clean.
Earlier "Never commit" was lifted: the user gave explicit per-change permission (short, plain messages, **no
AI/Claude attribution**). Session commits on top of `a07bc24`: `2d398c3` (v1 features), `934699d` (desktop
tools by default), `6b8374b` (skill-cmd extraction fix), `4574bf2` (security hardening).

What shipped beyond the Phase 1–7 baseline:
- **Skills now EXECUTE, not just retrieve.** Per turn the engine retrieves the best `SKILL-*.md`, narrows the
  offered tools, and **injects that skill's exact command into the user message** → small qwen3 reliably emits
  one `mcp.shell.run_command` call. A system-message hint was found to SUPPRESS tool-calling (0/5 → 4/4). 16
  desktop skills (brightness/volume/lock/suspend/night-light/DND/screenshot/media/clipboard/open-app/link/…).
- **Self-improving skills + hybrid RAG:** `skills_retrieval: hybrid` (keyword-first, local `nomic-embed-text`
  semantic fallback); `mcp.skills_writer.save_skill` + `/learn` write `skills/learned/`; `skills_feedback.py`
  logs returncode-aware outcomes; `/tidy` reorganizes.
- **Voice/audio:** TTS default = **SuperTonic-3** (`supertonic:M1`, Kokoro fallback); **barge-in default** via a
  new **PipeWire WebRTC AEC** backend (`pipewire_io.py`, `--half-duplex` to disable) — revisits the earlier
  "AEC infeasible" verdict (works as a PipeWire backend, just not via conda's raw-ALSA PortAudio).
- **UI:** native flowing multi-color GNOME-overlay orb (per-state), wake-word **conversation window** (say
  "computer" once → ~30 s of wake-free follow-ups), live voice switch via `mcp.voice`.
- **Security (no-sudo runtime — confirmed):** the running assistant never escalates; only `./ai-linux setup`
  uses sudo (one-time apt + `video` group + a **scoped `/dev/uinput` udev rule**, NOT the `input` group). Added
  a **catastrophic-only destructive-command denylist** (`shell_server._destructive_reason`, the single exec
  chokepoint — 24 blocked / 21 benign allowed), untrusted-content prompt framing, 0700 state/data dirs,
  randomized TTS temp names. Full model in **`SECURITY.md`**. The arm-based action gate + autonomy hard-floor
  are unchanged and remain the primary control.

**Remaining = user-only (NOT code):** run `./ai-linux setup` once (the one-time sudo), a live voice+GPU run,
and one GNOME log out/in (Wayland: overlay extension + AT-SPI window targeting). Deferred by design:
per-action confirmation modal (reserved `prompt_fn`), Ollama checksum pinning, heavier executor for complex
multi-step tasks.

## 2j. Reversible installs + remove the stray GNOME icon (2026-06-27, follow-up)

Two user requests after the live install: (1) a clean **uninstall/revert**, and (2) a new GNOME "Screenshot"
app-grid icon they didn't ask for.
- **Root cause of the icon:** `setup` apt-installed **`gnome-screenshot`**, which ships a visible `.desktop`
  (no `NoDisplay`). It is also broken on GNOME 50/Wayland. The orb + the two Shell extensions are intended.
- **Fix:** dropped `gnome-screenshot` from the default install; screenshots now use **`bin/ai-linux-screenshot`**,
  a stdlib MCP client that drives `computer-use-linux`'s sanctioned, pre-authorized screen-capture portal and
  saves a PNG to `~/Pictures` (no extra package, no app icon, no silent grab). The take-screenshot skill stays a
  shell skill so the reliable command-injection applies. (Remove the already-installed package once with
  `sudo apt-get remove -y gnome-screenshot`.)
- **Uninstall/revert ("inversion file"):** `setup` now records every system change to
  `~/.local/state/ai-linux/install-manifest.tsv`. **`./ai-linux uninstall`** (alias `revert`) reverts exactly
  those deltas — our files, both GNOME extensions, the udev rule, the `video`-group add, the apt packages WE
  installed — with a deterministic fallback when no manifest exists. `--dry-run` previews, `--keep-packages`
  skips apt, `--purge` also drops the conda env, Ollama models, weights, and config/data. Records are
  pre-existence-gated, so it never removes something that was already there.

## 3. Architecture

```
            ┌──────────────────────── assistant daemon (this repo) ─────────────────────────┐
  mic ─► [1] Voice I/O ─────► [2] Brain (Ollama, GPU) ──► router: SPEAK  ──► TTS ─► speaker
         always-on, VAD,       local LLM w/ tool-calling        │
         barge-in, streaming                                    └─► CALL TOOL
         (STT/TTS on CPU)                                            │
                                                          [3] Hands: Executor interface
                                                                     ├─► MCP: computer-use-linux (Wayland)   ← v1
                                                                     ├─► safe shell/file tools                ← v1
                                                                     └─► gptme / OpenCode / Goose             ← Phase 5
                                                          ⚠ safety gate: confirm before executing
                                                                     │
                                          [skills] retrieval interface ─► tiny RAG ← v1 seed; Fabric/AIChat ← Phase 6
            └───────────────────────────────────────────────────────────────────────────────┘
```

Three units, each with one job, a clear interface, and independent testability:

- **[1] Voice I/O** — continuous capture + VAD + barge-in + streaming STT and TTS, all on CPU. Replaces today's press-Enter logic. *(Pattern: GLaDOS loop; barge-in: RealtimeVoiceChat `turndetect`.)*
- **[2] Brain** — local LLM via Ollama with tool-calling, plus a **router** (answer vs call tool). Owns memory. *(Pattern: GLaDOS `llm_processor` / `tool_executor`.)*
- **[3] Hands** — calls everything through a generic **Executor interface**: an MCP tool (computer-use-linux) is just the *first* implementation; gptme/OpenCode/Goose plug into the same interface later. Every call passes the **confirm-before-execute** gate.

**The seams that carry the vision into v1:** the **Executor interface** (Phase 3) and the **retrieval interface** (Phase 4 seed) are the exact plug points Phases 5–6 attach to — no rewrite.

Reused from current repo: `utils.py` (ConfigManager / logging), `config.yaml` (extended), the working Ollama wiring. Retired: `app.py`, `assistant.py` (won't parse), `tts.py`, the press-to-talk `main.py` loop.

---

## 4. v1 — phased plan (through Phase 4)

### Phase 1 — Recon & cleanup
- Clone into untracked `refs/` (gitignored, never committed):
  - **Clone & study:** GLaDOS, computer-use-linux, RealtimeVoiceChat, Newelle, **Fabric**.
  - **Skim for patterns:** AIChat (RAG), OpenClaw (skills format).
- **Verify each claim on open** (don't trust the reports): repo exists, license, real capabilities, that computer-use-linux drives this exact GNOME/Wayland, that the model names exist.
- Read GLaDOS's loop + tool-calling; read computer-use-linux's MCP interface.
- Repo cleanup: delete broken legacy files; fix hardcoded foreign conda path (`/data1/cs24mtech14020/...`) in `run.sh`/`setup_env.sh`; set PersonaPlex aside; extend `config.yaml`; add daemon skeleton + `refs/` to `.gitignore`.
- **Exit test:** `refs/` cloned & verified; daemon skeleton imports and runs a no-op loop.

### Phase 2 — Always-on voice loop (talks)
- Swap batch Whisper → **streaming STT on CPU** (faster-whisper / RealtimeSTT).
- Add **fast CPU TTS** (Kokoro/Piper/Supertonic — pick the responsive one; keep Bark only if latency is acceptable).
- Brain: Ollama tool-calling model (start `llama3.2`) + router + memory. **Seam:** Brain exposes a clean `respond()` that can later hand off to an executor.
- Continuous VAD loop replaces press-to-talk. **No tools yet.**
- **Exit test:** speak → transcribed → answered → spoken back, hands-free, acceptable latency, LLM on GPU + STT/TTS on CPU fitting in budget.

### Phase 3 — Hands (does)
- Run **computer-use-linux** as an MCP server (separate process).
- Build the **Executor interface** with two first implementations: MCP client (computer-use-linux) + 2–3 safe shell/file tools.
- Wire the router's **CALL TOOL** path; implement the **confirm-before-execute safety gate**. **Seam:** the Executor interface is what gptme/OpenCode attach to in Phase 5.
- **Exit test:** a spoken simple task (e.g. "open Files", "what's the time", "create a note") executes after confirmation; refusal/timeout paths are graceful.

### Phase 4 — Hands-free polish & hardening
- **Barge-in:** interrupt the assistant mid-speech (port from RealtimeVoiceChat).
- **Optional wake-word** (Newelle / openWakeWord).
- Error recovery, resource cleanup, graceful shutdown, logging.
- **Tiny skills/procedure seed** behind a **retrieval interface** (a handful of SKILL-style entries + keyword retrieval). **Seam:** Fabric/AIChat attach here in Phase 6.
- **Exit test (= v1 done):** hands-free conversation that also executes simple tasks, stable across a session, recoverable from errors, fits 6 GB.

---

## 5. Beyond simplicity — post-v1 roadmap (Phases 5–7)

Each phase plugs into a v1 seam; none requires reworking the core.

### Phase 5 — Delegated executor (hard tasks)
- For tasks too complex for direct tool-calls, plug a real executor behind the **Phase-3 Executor interface**.
- **gptme** first (lightweight Python, tolerant of small local models). **OpenCode** as the stronger alternative once its repo/stars/local-support are verified. **Goose** only if a stronger/cloud model is in play.
- The router learns a third route: *answer | call tool | delegate to executor*.

### Phase 6 — Memory & skills
- A real skills/procedure layer behind the **Phase-4 retrieval interface**, seeded from **Fabric** patterns and **OpenClaw**-harvested SKILL entries.
- Retrieval starts tiny (keyword/embedding); grows toward **AIChat**'s hybrid RAG **only if** the tiny one proves insufficient. Adopt parts, not the whole tool.

### Phase 7 — Optional stronger brain
- Add an API/cloud LLM behind the **same Executor interface** for genuinely hard tasks, while the local model stays the default (TOP10's locked "API-LLM-ready later" lane). Everything in the default runtime path stays local.

---

## 6. Clone plan (reflecting "v1 + knowledge layer")

All clones live in untracked `refs/`, mined not merged, deleted only after their patterns are ported.

| Repo | When | Role | How used |
|---|---|---|---|
| GLaDOS | Phase 1 | voice-loop + in-loop tool-calling | **port the pattern** into units 1+2 |
| computer-use-linux | Phase 1 | Wayland desktop control | **run as MCP server** (unit 3) |
| RealtimeVoiceChat | Phase 1 | turn-detection / barge-in | **reference** for unit 1 |
| Newelle | Phase 1 | wake-word + click-to-approve UX | **reference** for unit 1 + safety gate |
| Fabric | Phase 1 | skills/procedure patterns | **clone & mine** for the Phase-6 skills seed |
| AIChat | Phase 1 (skim) | hybrid RAG patterns | **skim** for the retrieval interface |
| OpenClaw | Phase 1 (skim) | SKILL.md format / examples | **skim & harvest** skills; never run |
| gptme / OpenCode | Phase 5 | delegated executor | clone & wire behind Executor interface |
| Goose | Phase 5 (conditional) | heavyweight executor | only with a stronger model |

---

## 7. Open items to verify (don't trust the MD)

- OpenCode's real repo identity & star count (TOP10 handle `anomalyco/opencode` is suspect).
- That **computer-use-linux** is genuinely the only/viable Wayland control layer on this machine; fallback = AT-SPI + ydotool directly.
- Existence/quality of **Qwen3.5-4B**, **Supertonic-3 TTS**, "GNOME Shell 50.1" as written.
- Streaming-STT and fast-TTS choices that actually hit real-time on CPU here.

---

## 8. Truly out of scope (not even on the roadmap)

- Any **cloud LLM in the v1 default path** (only as an optional Phase-7 add-on).
- **OpenClaw as a runtime** (harvest skills only).
- **PersonaPlex** (7B full-duplex — fights the 6 GB ceiling and the modular design).
- LiveKit / networked transport; heavyweight multi-agent orchestration.
