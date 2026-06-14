# Open-Source Candidates — AI Linux Assistant (Wayland "Siri" Project)

> Master list of 27 verified open-source projects to evaluate as foundation / building blocks.
> All licenses, stars, and activity dates verified against the GitHub API on **2026-06-13**.
> Process: prune step by step → keep 2–3 finalists → start building.
>
> Verdict legend: 🟡 under review · ✅ keep · ❌ cut

---

## A. Voice / Desktop Assistants (Siri-like candidates)

| # | Project | Repo | License | Stars | Activity | What it is | Verdict |
|---|---------|------|---------|-------|----------|------------|---------|
| 1 | Newelle | [qwersyk/Newelle](https://github.com/qwersyk/Newelle) | GPL-3.0 | 1,369 | ✅ pushed 2026-06-05 | GTK4 GNOME chat assistant: voice in/out, wake word, Mini-Window overlay, Call Mode, runs terminal commands (click-to-approve), Ollama/llama.cpp, MCP, extensions. NOT an IDE (3-pane chat app). No daemon mode, no Wayland desktop control. | 🟡 |
| 2 | NyarchAssistant | [NyarchLinux/NyarchAssistant](https://github.com/NyarchLinux/NyarchAssistant) | GPL-3.0 | 402 | ✅ pushed 2026-05-19 | Newelle fork + anime avatar (Live2D) + VITS/Voicevox voices. Same dev, lags upstream. | 🟡 |
| 3 | Leon | [leon-ai/leon](https://github.com/leon-ai/leon) | MIT | 17,307 | ✅ pushed 2026-06-07 | Self-hosted assistant; local voice stack (faster-whisper, "Hey Leon" wake word, VITS TTS), shell/file tools, agent mode. BUT: unreleased one-man 2.0 rewrite, no Ollama support, last stable tag May 2023. | 🟡 |
| 4 | OpenVoiceOS | [OpenVoiceOS/ovos-core](https://github.com/OpenVoiceOS/ovos-core) | Apache-2.0 | 276 | ✅ pushed 2026-06-06, EU-funded | Mycroft successor: fully offline wake word + STT + TTS, skills run shell commands, Ollama persona. BUT: LLM cannot tool-call — actions need pre-defined intent phrases. | 🟡 |
| 5 | GLaDOS | [dnhkng/GLaDOS](https://github.com/dnhkng/GLaDOS) | MIT | 5,587 | ✅ pushed 2026-06-08 | Local realtime voice assistant: Silero VAD + Parakeet STT + Kokoro TTS, <600ms target, barge-in, MCP tool calls, Ollama llama3.2 documented default. Fits 6GB VRAM. No wake word (always-listening), Portal persona hardwired. | 🟡 |
| 6 | AgenticSeek | [Fosowl/agenticSeek](https://github.com/Fosowl/agenticSeek) | GPL-3.0 | 26,506 | ✅ pushed 2026-06-10 | "100% local Manus alternative": voice-enabled, plans tasks, browses, writes & executes code, Ollama supported. | 🟡 |
| 7 | QwenPaw | [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) | Apache-2.0 | ~17,500 | ✅ pushed 2026-06-12 | Alibaba AgentScope personal assistant; fully local deploy via Ollama/LM Studio, Whisper voice input. Rocket growth (17.5k stars in <4 months). | 🟡 |
| 8 | Row-Bot | [siddsachar/row-bot](https://github.com/siddsachar/row-bot) | Apache-2.0 | 1,271 | ✅ pushed 2026-06-12 | Local-first desktop assistant: realtime voice, shell access, browser automation, scheduling, knowledge graph memory, Ollama. Young but daily activity. | 🟡 |
| 9 | Moltis | [moltis-org/moltis](https://github.com/moltis-org/moltis) | MIT | 2,733 | ✅ pushed 2026-06-05 | Rust personal agent server, single binary: voice I/O (8 TTS + 7 STT providers), local models, every command sandboxed in Docker. | 🟡 |
| 10 | CAAL | [CoreWorxLab/CAAL](https://github.com/CoreWorxLab/CAAL) | MIT | 412 | ✅ pushed 2026-05-25 | Local voice assistant on LiveKit Agents; executes real actions via auto-discovered n8n workflows exposed as MCP tools. | 🟡 |
| 11 | RealtimeVoiceChat | [KoljaB/RealtimeVoiceChat](https://github.com/KoljaB/RealtimeVoiceChat) | MIT | 3,830 | ✅ active | Turnkey ~500ms local voice loop: RealtimeSTT (Whisper) + Ollama + Kokoro/Coqui TTS, real turn-taking/interruption, Dockerized. Conversation-only (no actions). | 🟡 |
| 12 | LocalAGI | [mudler/LocalAGI](https://github.com/mudler/LocalAGI) | MIT | 1,800 | ✅ v2.9.0 May 2026 | Self-hosted agent platform from the LocalAI author: MCP servers, custom actions, connectors, RAG, TTS/transcription hooks. | 🟡 |
| 13 | Seeva | [thisisharsh7/seeva-ai-assistant](https://github.com/thisisharsh7/seeva-ai-assistant) | MIT | 27 | ⚠️ pushed 2026-05-09 | Hotkey AI overlay (ask anywhere, dismiss). No voice, no command execution, tiny community. | 🟡 |
| 14 | azibom/assistant | [azibom/assistant](https://github.com/azibom/assistant) | GPL-3.0 | 14 | ❌ stale (2025-08-07) | Minimal GTK4 assistant: chat, run commands, notifications. Abandoned. | 🟡 |
| 15 | Local-Voice | [shashank2122/Local-Voice](https://github.com/shashank2122/Local-Voice) | MIT | 40 | ❌ one-shot demo (May 2025) | Vosk + Ollama + Piper offline voice chat. No actions, no maintenance. | 🟡 |

## B. Agentic Executors / Coding Agents (the "hands")

| # | Project | Repo | License | Stars | Activity | What it is | Verdict |
|---|---------|------|---------|-------|----------|------------|---------|
| 16 | OpenClaw | [openclaw/openclaw](https://github.com/openclaw/openclaw) | MIT | 378,340 | ✅ pushed 2026-06-12 | Personal AI assistant gateway (chat-channel-first): shell/file/app execution, skills (ClawHub), Ollama support. Caveats: no Linux voice client, wants ≥8–11GB-VRAM local models, Anthropic banned subscription OAuth (2026-04-04), 138+ CVEs. | 🟡 |
| 17 | OpenCode | [anomalyco/opencode](https://github.com/anomalyco/opencode) | MIT | 173,685 | ✅ pushed 2026-06-12 | Most-starred open terminal coding agent; TUI, 75+ providers incl. Ollama, build/plan modes. | 🟡 |
| 18 | OpenAI Codex CLI | [openai/codex](https://github.com/openai/codex) | Apache-2.0 | 90,688 | ✅ pushed 2026-06-12 | OpenAI's terminal agent; sandboxed exec, AGENTS.md memory. Tuned for GPT models (cloud). | 🟡 |
| 19 | Open Interpreter | [openinterpreter/open-interpreter](https://github.com/openinterpreter/open-interpreter) | Apache-2.0 | 63,874 | ✅ pushed 2026-06-10 | NL → executes code/shell locally. Now a Rust Codex-fork coding agent with bubblewrap/seccomp sandboxing + Ollama provider. (Voice project "01" is dead since Nov 2024; legacy Python version unmaintained.) | 🟡 |
| 20 | Cline | [cline/cline](https://github.com/cline/cline) | Apache-2.0 | 63,133 | ✅ pushed 2026-06-12 | VS Code agent + headless CLI; Plan/Act approval modes; BYOK any model. | 🟡 |
| 21 | Goose | [aaif-goose/goose](https://github.com/aaif-goose/goose) | Apache-2.0 | 49,078 | ✅ pushed 2026-06-12 | Rust general-purpose agent (Linux Foundation/AAIF): installs packages, orchestrates workflows, MCP extensions, CLI + desktop app. | 🟡 |
| 22 | Aider | [Aider-AI/aider](https://github.com/Aider-AI/aider) | Apache-2.0 | 46,072 | ⚠️ pushed 2026-05-22 | Git-native terminal pair programmer; auto-commits; any LLM incl. Ollama. Coding-only scope. | 🟡 |
| 23 | gptme | [gptme/gptme](https://github.com/gptme/gptme) | MIT | 4,328 | ✅ active | Terminal agent with local tools: shell, Python, files, browser; Ollama/llama.cpp local-first; Kokoro local TTS output. | 🟡 |

## C. Shell Helpers / Pipelines (lightweight utilities)

| # | Project | Repo | License | Stars | Activity | What it is | Verdict |
|---|---------|------|---------|-------|----------|------------|---------|
| 24 | Fabric | [danielmiessler/Fabric](https://github.com/danielmiessler/Fabric) | MIT | 42,319 | ✅ pushed 2026-06-09 | AI prompt-pattern framework for terminal pipelines (`cat x \| fabric -p summarize`). Not an assistant. | 🟡 |
| 25 | ShellGPT | [TheR1D/shell_gpt](https://github.com/TheR1D/shell_gpt) | MIT | 12,122 | ⚠️ pushed 2026-05-06 | Natural language → shell command generator; OpenAI + local models. | 🟡 |
| 26 | AIChat | [sigoden/aichat](https://github.com/sigoden/aichat) | Apache-2.0 | 10,129 | ⚠️ pushed 2026-02-23 | All-in-one Rust LLM CLI: 20+ providers, shell command gen, REPL, RAG, local server. | 🟡 |
| 27 | AI Shell | [BuilderIO/ai-shell](https://github.com/BuilderIO/ai-shell) | MIT | 5,257 | ⚠️ pushed 2026-01-05 | Simple NL → shell command CLI (Copilot CLI clone). | 🟡 |

---

## Excluded (kept for the record)

| Project | Reason |
|---------|--------|
| Claude Code (`anthropics/claude-code`) | Proprietary — not open source. (Still usable as a *service* executor via the Python `claude-agent-sdk`, but can't be a foundation.) |
| Gemini CLI | Code is Apache-2.0, but Google cuts off free/Pro/Ultra service on 2026-06-18 — dead end for individuals. |
| AIPointer (`gonemedia/aipointer`) | License `NOASSERTION` — not verifiably open source. |
| Linguflex (`KoljaB/Linguflex`) | No license file — legally unusable as a dependency; stale since 2025-06-17. |
| june (`mezbaul-h/june`) | MIT but dead since 2024-08-12; depends on abandoned Coqui TTS. |

---

## Context (decided so far)

- **Vision:** Siri-like assistant for Linux/Wayland (GNOME, Ubuntu 26.04). Fully local voice loop (local STT → small local LLM 4B/8B → Supertonic TTS); delegates complex execution to an agentic executor; hierarchical RAG of task procedures (= the existing **Agent Skills** / SKILL.md pattern, agentskills.io).
- **Hardware budget:** RTX 3060 Mobile **6GB VRAM**, 16GB RAM, i7-12700H.
- **Verified component picks (independent of foundation choice):** RealtimeSTT (STT+wake word, CPU), Qwen3.5-4B via Ollama (tool calling, fits 6GB fully on GPU), Supertonic-3 TTS (CPU, real-time, leaves VRAM free; Kokoro-82M fallback), agent-sh/computer-use-linux + Window Calls extension + portals/ydotool (Wayland desktop control), Agent Skills format for procedures.
- **Next step:** prune this list to 2–3 finalists, then start the build.
