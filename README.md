# AI Linux Assistant

A **local-first, Wayland-native voice assistant** for Linux that you can *talk to* — and that can *act* on
your machine (open apps, run commands, answer questions) through a confirmed, safety-gated tool layer.

Everything in the runtime path runs **locally and open-source**. No cloud, no API keys required.
Tuned to fit a **6 GB GPU** (RTX 3060 Mobile) by keeping the LLM on the GPU and speech on the CPU.

> Built by vendoring & evolving the excellent [dnhkng/GLaDOS](https://github.com/dnhkng/GLaDOS) engine.
> Design notes & decisions: [PLAN.md](PLAN.md). (An agent-onboarding `CLAUDE.md` is kept locally.)

---

## ✨ Features
- **Wake-word voice loop** with VAD (say "computer …"; **talk-over barge-in is on by default** via PipeWire echo-cancel — see [Barge-in](#-barge-in-interrupting-the-assistant)).
- **Voice *and* text** input (`input_mode: both`).
- **Local brain** — `qwen3:4b` via [Ollama](https://ollama.com) (GPU); switch to the lighter `qwen3:1.7b` via the Settings panel / `GLADOS_LLM_MODEL`.
- **CPU speech** — Parakeet ASR + SuperTonic TTS (Kokoro fallback), all ONNX, so the GPU stays free for the LLM.
- **Acts on your desktop** — Wayland control (AT-SPI / portals / ydotool) + a shell executor, as MCP tools.
- **Safety gate** — irreversible actions are denied unless you explicitly arm them; a destructive-command denylist backs it up.
- **Skills that run** — a procedure library the model retrieves at runtime (keyword-first **hybrid RAG**) and the matched command is injected so the model actually executes it; it can also **learn new skills** (`/learn`).
- **On-screen overlay** (GNOME Shell extension) — top-right orb + transcript that tracks state, with
  listening-mode controls (always / wake / click) that can hand the mic back to other apps.

---

## 🧠 Architecture

```mermaid
flowchart LR
    mic(["🎙 Microphone"]) --> vad["Silero VAD<br/>(always-listening)"]
    vad --> asr["Parakeet ASR<br/>ONNX · CPU"]
    asr --> brain{"qwen3:4b / Ollama<br/>GPU · router"}
    brain -->|"answer"| tts["SuperTonic TTS<br/>ONNX · CPU"]
    tts --> spk(["🔊 Speaker"])
    brain -->|"call tool"| gate["Action safety gate"]
    gate --> mcp["MCP tool servers"]
    mcp -->|"result"| brain
```

The brain (LLM) is the only component on the GPU; ASR, VAD, and TTS run on the CPU as ONNX — that split
is what makes the assistant fit in 6 GB of VRAM.

### A conversational turn

```mermaid
sequenceDiagram
    actor U as User
    participant V as Voice I/O (CPU)
    participant B as Brain (Ollama · GPU)
    participant G as Safety gate
    participant T as MCP tool
    U->>V: speaks
    V->>B: transcript (Parakeet ASR)
    alt just answer
        B->>V: response text
        V->>U: speech (SuperTonic TTS)
    else perform an action
        B->>G: tool call
        G->>T: run (only if armed)
        G-->>B: denied (if not armed / autonomy)
        T-->>B: result
        B->>V: spoken summary
    end
```

---

## 🚀 Quick start

One script does everything — install and run:

```bash
./ai-linux setup     # one-time: conda env, deps, Ollama + qwen3:4b, ONNX weights, GNOME launcher + icon
./ai-linux           # voice + text  (auto-starts Ollama; on a fresh machine it self-runs setup first)
```

Then, day to day:

```bash
./ai-linux            # voice + text, local qwen3:4b brain      [default]
./ai-linux tui        # text-only Textual UI
./ai-linux doctor     # check everything is ready
./ai-linux download   # (re)fetch the ONNX speech weights
./ai-linux say "hi"   # speak a phrase and exit
./ai-linux uninstall  # revert everything setup changed (--dry-run to preview; --purge to also drop env/models/weights)
# flags: --groq (cloud brain)   --local (default)   --no-actions (don't arm shell/desktop actions)
#        --half-duplex (turn off voice barge-in)   --overlay-mode always|wake|click
```

Or click **AI Linux Assistant** in the GNOME app grid (installed by `setup`, with a custom glass-orb icon).
`setup` is idempotent — safe to re-run, and it records every system change to
`~/.local/state/ai-linux/install-manifest.tsv` so `./ai-linux uninstall` cleanly reverts exactly those changes.
Speech weights download on first `setup` only if not already present.

---

## 🔧 Configuration

All settings live in [`configs/ai_linux_config.yaml`](configs/ai_linux_config.yaml) (top key `Glados:`):

| Setting | What it does |
|---|---|
| `llm_model` / `completion_url` | brain model + endpoint (default local Ollama `qwen3:4b`) |
| `voice` | TTS voice: `supertonic:M1` (default; male `M1`/`M3`–`M5`, female `F1`–`F5`) or a Kokoro voice e.g. `am_michael` |
| `asr_engine` | `ctc` (faster) or `tdt` (more accurate) — both CPU |
| `input_mode` | `audio`, `text`, or `both` |
| `interruptible` | barge-in (interrupt the assistant by speaking) |
| `wake_word` | a trigger phrase, or `null` for always-listening |
| `personality_preprompt` | system prompt / persona |
| `mcp_servers` | the tools the assistant can call |

### Local or Groq brain

Two interchangeable brains — **speech, tools, and the safety gate are identical; only the LLM differs**:

| Brain | Config | Run | Notes |
|---|---|---|---|
| **Local** (default) | `configs/ai_linux_config.yaml` | `./ai-linux` | `qwen3:4b` via Ollama (GPU); lighter `qwen3:1.7b` via Settings / `GLADOS_LLM_MODEL` |
| **Groq API** | `configs/ai_linux_groq.yaml` | `export GROQ_API_KEY=… && ./ai-linux --groq` | faster/stronger cloud model; key read from env, never stored |

Any other OpenAI-compatible endpoint works too — point `completion_url`/`api_key` at it. **Local stays the
default focus.**

---

## 🛠 Tools (MCP servers)

Tools are exposed to the model as `mcp.<server>.<tool>`.

| Server | Tools | Purpose | Gated |
|---|---|---|:---:|
| `system_info` | `cpu_load`, `memory_usage`, `temperatures`, … | system stats | — |
| `time_info` | `now_iso`, `uptime_seconds`, … | time / uptime | — |
| `memory` | — | conversation memory | — |
| `skills` | `list_skills`, `find_skill` | retrieve a procedure (the relevant command is also auto-injected per turn) | — |
| `skills_writer` | `save_skill` | learn a new skill — writes a markdown draft only, never runs anything | — |
| `voice` | `list_voices`, `set_voice` | change the assistant's own TTS voice live | — |
| **`shell`** | `run_command` | run a local command (as you, never sudo) | ✅ |
| **`computer_use`** | click / type / window / screenshot … | Wayland desktop control | ✅ |

### Safety gate

Gated tools (shell + desktop control) are **off by default** and fail safe:

```mermaid
flowchart TD
    A["Tool call"] --> B{"Gated family?<br/>mcp.shell.* / mcp.computer_use.*"}
    B -->|"no"| RUN["✅ Run tool"]
    B -->|"yes"| C{"Autonomy mode?"}
    C -->|"yes"| HF["⛔ Deny — hard floor"]
    C -->|"no"| D{"GLADOS_ALLOW_ACTIONS set?"}
    D -->|"yes"| RUN
    D -->|"no"| AD["⛔ Deny — arm to enable"]
```

Interactive launches (`./ai-linux`, the GNOME app icon, `./ai-linux tui`) **arm actions by default** so the
assistant can actually act — mute the device, open apps, run commands. Disable with `./ai-linux --no-actions`.
The autonomous loop can **never** run gated actions, regardless of settings (hard floor).

### No superuser

The **running assistant never uses `sudo`/root** — every command runs as your user (verified: there is no
`sudo` call anywhere in the runtime). The **only** place sudo is used is `./ai-linux setup`, which does a
one-time `apt` install of a few user-level desktop tools (`brightnessctl`, `playerctl`, `wl-clipboard`,
`ydotool`) so brightness / media / clipboard / input work — skipped automatically if they're already present.
(Screenshots go through `computer-use-linux`'s sanctioned screen-capture portal; `gnome-screenshot` is **not**
installed — it adds a stray app-grid icon and is broken on GNOME Wayland.) Setup also scopes `ydotool`'s input
access via a **udev rule** (per-session ACL on `/dev/uinput`), not the broad `input` group, so nothing gains
system-wide keystroke read. Every system change setup makes is recorded so **`./ai-linux uninstall`** reverts
exactly those deltas — installs are fully and transparently reversible.

A destructive-command **denylist** (`mcp/shell_server.py`) refuses clearly catastrophic commands
(`rm -rf ~`, `dd of=/dev/…`, `mkfs`, fork bomb, `curl … | sh`, …) regardless of how they were produced.
See **[SECURITY.md](SECURITY.md)** for the full threat model, guarantees, and how to run disarmed
(`./ai-linux --no-actions`).

---

## 🗣 Barge-in (interrupting the assistant)

**On by default:** `./ai-linux` runs **full-duplex** so you can talk over the assistant on open speakers —
your voice cuts it off mid-sentence (TTS is cancelled the instant VAD fires). It works because the launcher
routes audio through **PipeWire's WebRTC echo-cancel** (`module-echo-cancel`): capture goes through `pw-record`
and TTS plays through `pw-play` to an echo-cancelled sink, so the open mic never transcribes the assistant's
own voice. No system packages and **no `sudo`** — the AEC module is loaded as your user for the session and
unloaded on exit (fully reversible).

Why a separate PipeWire backend (`audio_io/pipewire_io.py`) instead of the in-process path: conda's PortAudio
only enumerates raw `hw:` ALSA cards and can't open PipeWire's `pipewire`/`pulse` PCMs, so it can't be routed
through `module-echo-cancel` directly. The PipeWire backend sidesteps that.

`./ai-linux --half-duplex` turns barge-in **off** — the mic is ignored while the assistant speaks (the echo
guard) and re-opens after a short hangover. Use it if AEC misbehaves on your hardware, or just use headphones
(a headset gives clean input with no echo to cancel).

---

## 🪟 On-screen overlay (optional)

A GNOME Shell extension shows a top-right **orb + transcript** that tracks the assistant's state
(idle / listening / thinking / speaking), with a mode header:

| Mode | Behavior | Sound card |
|---|---|---|
| **Always** | continuous listening | held by the assistant |
| **Wake** | acts only on a keyword (default "computer") | held (to hear the keyword) |
| **Click** | click the orb to talk one turn | **released between turns → other apps can use it** |
| **Mute** | instant pause | **released** |

`./ai-linux setup` installs and enables it; **log out and back in once** so GNOME loads it (Wayland only
loads new extensions at login). The orb is drawn natively by the Shell (not the browser Rive demo). Run
without it via `./ai-linux --no-overlay`, or pick the starting mode with `--overlay-mode=click`.

## 📁 Project layout

```
ai-linux                       # single launcher + installer (setup · doctor · run)
configs/ai_linux_config.yaml   # active config  (+ ai_linux_groq.yaml for the Groq brain)
skills/                        # SKILL-*.md procedures (served by mcp.skills)
ui/                            # persona.html demo, icon/, gnome-extension/ (on-screen overlay)
src/glados/                    # vendored GLaDOS engine (core/ mcp/ overlay/ ASR/ TTS/ audio_io/ …)
models/                        # model configs + ONNX speech weights (weights gitignored)
data/                          # ASR warm-up sample + demo assets
PLAN.md                        # design notes & decisions  (CLAUDE.md = local agent guide)
```

---

## 📌 Status
v1 is built, committed, and verified hands-on (configs load; safety gate incl. the autonomy hard-floor; all
MCP servers; launcher + CLI wiring). The runtime is fully provisioned locally — Ollama + `qwen3:4b` and all
ONNX speech weights are already in place — so **the one remaining step is the first live voice run** (mic +
GPU). See [PLAN.md](PLAN.md) for the roadmap (delegated executor, richer memory/RAG, per-action voice confirmation).

## 🙏 Credits & licenses
- Engine: **[dnhkng/GLaDOS](https://github.com/dnhkng/GLaDOS)** (MIT) — vendored; see [`LICENSE.GLaDOS`](LICENSE.GLaDOS).
- Desktop control: **[agent-sh/computer-use-linux](https://github.com/agent-sh/computer-use-linux)** (MIT).
- Default TTS: **[supertone-inc/supertonic](https://github.com/supertone-inc/supertonic)** (code MIT; weights OpenRAIL-M) — ONNX, fetched once on first use.
- Speech: Parakeet (ASR), SuperTonic + Kokoro (TTS), Silero (VAD). Brain: Ollama + Qwen3 / Llama 3.2 (local) or [Groq](https://groq.com) (API).
- Pattern references: Newelle, RealtimeVoiceChat, Fabric, AIChat.

Vendored components retain their original licenses.
