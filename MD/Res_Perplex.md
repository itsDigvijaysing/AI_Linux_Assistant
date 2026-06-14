I now have comprehensive research data on all 27 projects. Let me create the full analysis.

***

# Complete Analysis: 27 Open-Source Candidates for the AI Linux/Wayland "Siri" Project

***

## Section A — Voice / Desktop Assistants

***

### #1 · Newelle — GTK4 GNOME Chat Assistant
**What it is:** A native GNOME AI assistant with a three-pane chat layout, not an IDE. It's the closest thing to a first-party Linux desktop AI widget. [forum.zorin](https://forum.zorin.com/t/gnomes-newelle-ai-assistant/49836)

**Stack & How it works:** Built in Python + GTK4, it uses GNOME's libadwaita for theming. The voice pipeline runs Whisper (STT) + Piper/Coqui (TTS), and the backend talks to Ollama, llama.cpp, or any OpenAI-compatible API. A Mini-Window mode creates a floating overlay so you can invoke it from any GNOME workspace. A "Call Mode" mirrors phone-call-style continuous conversation. Terminal commands appear as click-to-approve blocks — the LLM proposes, you confirm. [github](https://github.com/qwersyk/newelle)

**Unique Points:**
- Wake-word listener built in; MCP tool extension support
- Extension API lets third-party devs add new backends or UI panels
- Ships as a Flatpak — zero-friction install on Ubuntu 26.04 / GNOME
- Active upstream (1,400+ commits); GPL-3.0 permits forking for your own daemon [github](https://github.com/qwersyk/newelle)

**Verdict relevance for your project:** Strong fit as a *UI shell* — its click-to-approve terminal tool is already the Wayland-safe approval modal you need. Lacks a daemon mode and Wayland desktop control natively, so you'd bolt on `ydotool` / portals externally.

***

### #2 · NyarchAssistant — Newelle Fork + Anime Avatar
**What it is:** A Newelle fork maintained by the NyarchLinux team, adding a Live2D animated avatar and VITS/Voicevox Japanese-centric voice synthesis. [github](https://github.com/qwersyk/newelle)

**Stack & How it works:** Inherits Newelle's full GTK4 stack and then layers the Live2D Cubism SDK for a reactive animated character displayed alongside the chat. Voicevox (Japanese neural TTS) and VITS are added as first-class voice providers. Because it's a downstream fork of the same developer, it lags upstream Newelle by ~2–4 weeks on new features. [github](https://github.com/qwersyk/newelle)

**Unique Points:**
- Only open-source GNOME assistant with a Live2D animated character persona
- Voicevox support enables high-quality Japanese TTS natively
- Shares Newelle's Ollama/MCP/voice pipeline without separate maintenance burden

**Verdict relevance:** Useful primarily if the project requires an animated avatar layer; otherwise Newelle upstream is strictly superior. Cut unless you want the persona display.

***

### #3 · Leon — Self-Hosted Personal Assistant (v2.0 Rewrite)
**What it is:** A long-running self-hosted personal assistant built on Node.js + Python. The v1 "master" branch is stable but pre-agentic (no LLM tool-calling). The v2 "develop" branch is a ground-up 2026 agentic rewrite. [github](https://github.com/leon-ai/leon/blob/develop/README.md)

**Stack & How it works:** v1 uses faster-whisper (STT), a custom "Hey Leon" wake word, VITS TTS, and a module/skill system for shell and file operations. v2 is being rebuilt around an LLM-first agent loop with a TCP server, Python bridge, and Node.js bridge as separate microservices communicating over IPC. The developer (Louis Grenard) is a solo maintainer sharing progress primarily on X. [github](https://github.com/leon-ai/leon/releases)

**Unique Points:**
- One of the oldest open-source personal assistants (started 2019), 17K+ stars signals strong community knowledge base
- "Hey Leon" wake word is baked in — no extra library needed
- v2 architecture separates core, bridges, and skills cleanly for modular integration [github](https://github.com/leon-ai/leon/blob/develop/README.md)

**Verdict relevance:** The v2 rewrite's TCP/bridge pattern is architecturally elegant but the "no Ollama support + unreleased" status means it's a 6–12 month risk. Strong inspiration source; weak foundation candidate right now.

***

### #4 · OpenVoiceOS (OVOS) — Mycroft Successor Platform
**What it is:** A community-driven fork of Mycroft AI, rebuilt as a fully offline voice AI platform. EU-funded (NGI Zero Commons grant) and governance-stable. [openvoiceos](https://www.openvoiceos.org)

**Stack & How it works:** OVOS runs a modular bus-based architecture (`messagebus`) where every component — wake word engine (Precise/OpenWakeWord/Porcupine), STT (Whisper/Vosk/Kaldi), TTS (Piper/Mimic3/Coqui), skill runner — is a separate Python process communicating over a local MQTT-style bus. Skills are Python classes that register intent handlers. An Ollama "persona" backend adds LLM conversation, but LLM responses cannot trigger skill tool-calls — only user utterances matching defined intent patterns execute actions. [github](https://github.com/OpenVoiceOS/ovos-core)

**Unique Points:**
- Most battle-tested fully-offline wake-word + STT + TTS stack in open source
- Raspberry Pi and embedded device support; runs headless with no GPU
- Skill ecosystem inherited from Mycroft (thousands of community skills)
- NGI Zero funding ensures continuity beyond any single developer [cnx-software](https://www.cnx-software.com/2025/02/24/the-openvoiceos-foundation-aims-to-enable-open-source-privacy-and-customization-for-voice-assistants/)

**Verdict relevance:** Best-in-class offline voice pipeline, but the LLM ↔ tool-call gap is a hard architectural constraint. Ideal as a *voice pipeline component donor* (steal its STT/TTS/wake-word patterns) rather than as a top-level foundation.

***

### #5 · GLaDOS — Realtime Local Voice Assistant
**What it is:** A high-performance fully-local voice assistant built around Portal's GLaDOS character, engineered for sub-600ms end-to-end latency. [github](https://github.com/dnhkng/GLaDOS)

**Stack & How it works:** The pipeline is: Silero VAD (always-on voice activity) → Parakeet STT → Ollama llama3.2 (default) → Kokoro TTS, all running concurrently in Python asyncio threads. Barge-in detection lets you interrupt a speaking response. MCP tool calling is wired directly into the LLM's tool-use loop — the model can call MCP servers to execute real actions. The whole stack fits in 6GB VRAM. [youtube](https://www.youtube.com/watch?v=mB1KqGfmTgk)

**Unique Points:**
- <600ms response target with barge-in/interruption — the fastest local pipeline in this list
- MCP tool calls natively in the voice loop (not just chat-based)
- Dockerized with `docker-compose` for clean install
- No wake word (always-listening) — a design choice requiring modification for your use case [github](https://github.com/dnhkng/GlaDOS/issues/182)

**Verdict relevance:** The voice loop architecture is *directly applicable* to your project. The Portal persona is hardwired into prompts but trivially replaceable. Strongest candidate for the voice layer foundation.

***

### #6 · AgenticSeek — 100% Local Manus Alternative
**What it is:** A fully local autonomous agent with a web UI, described as a "JARVIS-like" voice-enabled planner that browses, writes code, and executes shell commands — zero cloud dependencies. [github](https://github.com/Fosowl/agenticSeek)

**Stack & How it works:** Python backend with a React web frontend. An `llm_router` module dispatches tasks to specialized sub-agents (browser agent, code agent, file agent). Voice input goes through local Whisper STT, and the planner uses an LLM (Ollama supported) to break tasks into steps. A SearxNG container handles private web search. Multi-agent orchestration allows complex chained workflows. [github](https://github.com/Fosowl/agenticSeek/issues)

**Unique Points:**
- Developed on RTX 3060 hardware — directly benchmarked on *your exact GPU* [news.ycombinator](https://news.ycombinator.com/item?id=43805457)
- Multi-agent router with specialized sub-agents (browser, code, file, shell)
- SearxNG private web search built in
- 26K+ stars with very active issue tracker (security issues flagged and actively addressed) [github](https://github.com/Fosowl/agenticSeek/issues)

**Verdict relevance:** Excellent as a *task executor* layer. Its multi-agent router pattern is powerful for complex tasks. The web UI is not a Wayland desktop app, but the Python backend is fully embeddable.

***

### #7 · QwenPaw — Alibaba AgentScope Personal Assistant
**What it is:** Alibaba's AgentScope team's open-source personal AI assistant, offering a full local-or-cloud deployable agent with persistent memory, multi-channel support, and a Skills Hub. [aihub](https://www.aihub.cn/agents/copaw/)

**Stack & How it works:** Built on the AgentScope framework (Python). Memory is managed by ReMe — a hybrid vector (0.7 weight) + BM25 keyword (0.3 weight) retrieval system with time-tiered compression. A "proactive heartbeat" mechanism allows the agent to autonomously run scheduled tasks (e.g., push a morning digest at 8AM) without user prompting. Supports Ollama and llama.cpp for fully local inference. Skills are Python modules that hot-plug into the agent at runtime with MCP tool support. [ai-bot](https://ai-bot.cn/qwenpaw/)

**Unique Points:**
- Only assistant in this list with a *proactive heartbeat* — can do work without being asked
- ReMe memory: long-term, editable file-based memory with hybrid retrieval + multi-agent isolation [agentscope](https://agentscope.io/blog/qwenpaw-vs-openclaw/)
- Rocket adoption (17.5K stars in <4 months) signals strong ecosystem momentum [ai-bot](https://ai-bot.cn/qwenpaw/)
- Multi-channel: Discord, DingTalk, Feishu, iMessage, QQ from one agent instance [aihub](https://www.aihub.cn/agents/copaw/)

**Verdict relevance:** The persistent memory architecture and Skills Hub pattern closely mirror your SKILL.md / Agent Skills vision. Strong candidate for the memory and skills layer.

***

### #8 · Row-Bot — Local-First Desktop AI Assistant
**What it is:** A young but rapidly-developed local-first desktop AI assistant with voice, shell, browser automation, scheduling, and a personal knowledge graph. [github](https://github.com/siddsachar)

**Stack & How it works:** Python desktop app. Voice input (realtime) feeds an Ollama-backed LLM. The knowledge graph is stored as Markdown files with Obsidian-style backlinks — meaning it's human-editable and version-controllable. Browser automation, shell access, and scheduled tasks are all built in. Voice memos are auto-transcribed and wired into the graph. [github](https://github.com/rowboatlabs/rowboat)

**Unique Points:**
- Knowledge graph stored as plain Markdown — fully portable, diff-able, no database lock-in [reddit](https://www.reddit.com/r/selfhosted/comments/1r3z747/opensource_localfirst_ai_coworker_that_builds_a/)
- Obsidian-compatible — existing Obsidian vaults can serve as the memory base
- Voice memos auto-update the graph: speak → content captured → searchable immediately [facebook](https://www.facebook.com/0xSojalSec/posts/opensource-ai-coworker-turns-your-work-into-knowledgeable-graphsan-ai-coworker-t/1529594915361573/)
- Daily commit activity despite being a young project [github](https://github.com/siddsachar)

**Verdict relevance:** The Markdown knowledge graph is a great match for your Agent Skills / SKILL.md format. However, the project is very young and the desktop-app architecture may not expose a clean daemon API.

***

### #9 · Moltis — Rust Personal Agent Server
**What it is:** A single-binary Rust personal agent server — no Node.js, no npm, no runtime dependencies. Everything (web UI, LLM routing, voice, tools) compiles into one executable. [github](https://github.com/moltis-org)

**Stack & How it works:** Written entirely in Rust. The binary bundles a web UI (WebSocket streaming), a trait-based LLM provider architecture (OpenAI, Anthropic, Ollama, etc.), and a sandboxed execution layer using Docker or Apple Containers. Voice: 8 TTS + 7 STT providers including local options. Long-term memory uses embeddings with hybrid vector + full-text search and auto-compaction. MCP tool servers connect over stdio or HTTP/SSE. [everydev](https://www.everydev.ai/tools/moltis)

**Unique Points:**
- *Single Rust binary* — the lowest-overhead deployment in this entire list; no interpreter needed [agentskill](https://agentskill.work/en/skills/moltis-org/moltis)
- Every LLM command runs in Docker sandbox — strongest security posture by default [everydev](https://www.everydev.ai/tools/moltis)
- Multi-channel out of the box: Telegram, WhatsApp, Discord, Teams, Web UI from one server [yro](https://yro.ai/agents/moltis)
- Inspired by OpenClaw but rewritten for security-first design [github](https://github.com/moltis-org)

**Verdict relevance:** Ideal as a *persistent background server* on your machine. The Rust binary + Docker sandboxing makes it the most production-stable local agent server. Caveat: Wayland desktop control requires adding external MCP tools.

***

### #10 · CAAL — LiveKit Voice Assistant with n8n MCP Tools
**What it is:** A local voice assistant built on LiveKit Agents (WebRTC), with n8n workflows auto-discovered as MCP tools — allowing unlimited tool expansion without code changes. [github](https://github.com/CoreWorxLab/CAAL)

**Stack & How it works:** LiveKit handles WebRTC audio transport for low-latency voice. The STT layer uses Speaches (Faster-Whisper), TTS uses Kokoro, and the brain is Ollama (Ministral-3:8B default, with a fine-tuned `caal-ministral` model available). Wake word detection via Picovoice Porcupine ("Hey Cal"). n8n workflows are exposed as MCP tools and **auto-discovered** at runtime — add a new n8n workflow, and CAAL picks it up immediately, no restart needed. CAAL can even create new n8n workflows via voice command (self-modifying). [newreleases](https://newreleases.io/project/github/CoreWorxLab/CAAL/release/v1.0.0)

**Unique Points:**
- **Self-modifying**: can create its own new tools via voice [youtube](https://www.youtube.com/watch?v=6uEM8yOzZrU)
- n8n auto-discovery means unlimited tools without touching the assistant code
- Credentials stay in n8n's encrypted store — the LLM never sees API keys [mcpmarket](https://mcpmarket.cn/server/69507f62aa5ba1672820e4d3)
- Android app available for multi-room distributed voice endpoints [youtube](https://www.youtube.com/watch?v=6uEM8yOzZrU)

**Verdict relevance:** The n8n → MCP auto-discovery pattern is brilliant for your extensibility goal. The fine-tuned voice-tool-calling model is a unique advantage. Excellent candidate for the tool-integration layer.

***

### #11 · RealtimeVoiceChat — ~500ms Local Voice Loop
**What it is:** A turnkey, Dockerized local voice chat system engineered purely for conversation latency — not for action execution. [github](https://github.com/KoljaB/RealtimeVoiceChat)

**Stack & How it works:** Client-server architecture: browser captures audio → WebSocket streams chunks to Python FastAPI backend → RealtimeSTT (Whisper-based) transcribes → Ollama LLM generates response → RealtimeTTS (Kokoro/Coqui/Orpheus) synthesizes → audio streamed back to browser. A `turndetect.py` module handles dynamic silence detection for natural conversation turn-taking with barge-in support. [zenn](https://zenn.dev/kun432/scraps/02bdaaa7f01cfa)

**Unique Points:**
- ~500ms end-to-end latency reliably demonstrated at 24B model scale [news.ycombinator](https://news.ycombinator.com/item?id=43899029)
- Smart turn-taking with barge-in interruption via `turndetect.py`
- One-command Docker Compose setup — lowest barrier to running a local voice loop [youtube](https://www.youtube.com/watch?v=9jnQykg-JTE)
- Uses same RealtimeSTT and RealtimeTTS libraries by KoljaB that are in your verified component picks [github](https://github.com/KoljaB/RealtimeVoiceChat)

**Verdict relevance:** This is essentially a *packaged reference implementation* of exactly your planned STT → LLM → TTS loop. Use it as the voice loop template and add action execution on top.

***

### #12 · LocalAGI — Self-Hosted Agent Platform (from LocalAI author)
**What it is:** A self-hostable AI agent orchestration platform rewritten in Go (v2) by mudler — the same developer as LocalAI. [github](https://github.com/mudler/LocalAGI)

**Stack & How it works:** Go backend + React frontend. Agents are configured via a no-code web UI — define roles, model assignments, tool access, and multi-agent "groups" graphically. The platform is OpenAI Responses API compatible, so any LLM served by LocalAI (llama.cpp, Transformers) plugs in. MCP servers connect for tool execution. LocalRecall handles long-term memory. Custom actions are scriptable in Go. Platform supports Discord, Slack, Telegram, GitHub Issues, IRC connectors. [github](https://github.com/mudler/LocalAI/discussions/5184)

**Unique Points:**
- No-code agent creation UI — the most accessible way to wire multi-agent workflows [sourcepulse](https://www.sourcepulse.org/projects/1831583)
- Drop-in OpenAI Responses API replacement keeps vendor-switching cost near zero
- Agent teaming: multiple agents with different roles can collaborate on complex tasks [sourcepulse](https://www.sourcepulse.org/projects/1831583)
- From the LocalAI author — deep integration with the widest local model backend ecosystem [github](https://github.com/mudler/LocalAI/discussions/5184)

**Verdict relevance:** Best choice if you want a *visual control plane* for your agent network. The multi-agent teaming and no-code UI are strong for building procedure pipelines.

***

### #13 · Seeva — Hotkey AI Overlay
**What it is:** A minimal hotkey-triggered AI overlay that pops up on any screen when you press a keybind — ask a question, get an answer, dismiss. [github](https://github.com/qwersyk/newelle)

**Stack & How it works:** A lightweight desktop widget (likely Electron or web-based). No voice, no command execution — pure text Q&A overlay. Last commit May 2026, but only 27 stars and effectively a solo project.

**Unique Points:**
- Extremely low overhead — just an overlay widget
- Hotkey invocation pattern is useful as a UX reference for your own overlay design

**Verdict relevance:** ❌ Cut. Too minimal, no voice, no actions, tiny community. Useful only as UX inspiration for the hotkey-summon pattern.

***

### #14 · azibom/assistant — Minimal GTK4 Assistant
**What it is:** A minimal GTK4 chat assistant with basic shell command execution and notifications.

**Stack & How it works:** GTK4 Python, minimal feature set — chat panel, run commands, desktop notifications.

**Verdict relevance:** ❌ Cut immediately. Last push August 2025, effectively abandoned. Zero community, minimal features.

***

### #15 · Local-Voice — One-Shot Voice Demo
**What it is:** A proof-of-concept offline voice chat demo using Vosk + Ollama + Piper.

**Stack & How it works:** Simple Python script. Vosk for STT (CPU, grammar-limited), Piper for TTS, Ollama for LLM. No actions, no maintenance.

**Verdict relevance:** ❌ Cut. A demo script, not a project. Vosk is outclassed by your already-chosen RealtimeSTT. No value over your verified components.

***

## Section B — Agentic Executors / "The Hands"

***

### #16 · OpenClaw — Personal AI Assistant Gateway
**What it is:** A chat-channel-first AI agent gateway (formerly Clawdbot/Moltbot) that delivers a personal assistant via Telegram, WhatsApp, Discord, or CLI, with a 700+ skill ecosystem on ClawHub. [docs.openclaw](https://docs.openclaw.ai/tools/skills)

**Stack & How it works:** Node.js monolith. Skills are SKILL.md bundles — structured markdown + supporting files that define tool behaviors. The agent reads skills at startup, and the LLM reasons over them to select and invoke tools. Shell/file/app execution is supported with sandboxed or full-access modes. Ollama is supported for local inference. [docs.openclaw](https://docs.openclaw.ai/clawhub)

**Unique Points:**
- **700+ skills on ClawHub** — largest ecosystem of SKILL.md format tools, directly compatible with your Agent Skills vision [reddit](https://www.reddit.com/r/AI_Agents/comments/1r2u356/best_openclaw_skills_you_should_install_from/)
- The SKILL.md format is the *exact same standard* as agentskills.io / your project's procedure format
- Multi-channel: your assistant reaches you on any messenger from the same server [openclaw](https://openclaw.ai)
- Critically: 138+ CVEs in the Node.js dependency tree, and Anthropic banned their OAuth flow — need careful security hardening [docs.openclaw](https://docs.openclaw.ai/tools/skills)

**Verdict relevance:** The skill ecosystem alignment is *uniquely valuable*. The security issues are real but manageable by running air-gapped locally. ✅ Keep as skills ecosystem reference; evaluate carefully as a runtime foundation.

***

### #17 · OpenCode — Most-Starred Open Terminal Coding Agent
**What it is:** The most-starred open-source terminal coding agent (173K+ stars), built in Go by the SST/Anomaly team. [opencode](https://opencode.ai)

**Stack & How it works:** Go binary with a TUI (terminal UI). Two modes: **Build agent** (reads, writes files, runs commands) and **Plan agent** (read-only, asks before any change). Supports 75+ providers via a unified API abstraction including Ollama. LSP integration means it understands your codebase structure. MCP support built in. Desktop app and IDE extension also available. [lushbinary](https://lushbinary.com/blog/opencode-developer-guide-terminal-ai-coding-agent/)

**Unique Points:**
- Build vs Plan mode separation is the best UX for supervised agentic execution
- 75+ providers — most flexible model routing in this category [morphllm](https://www.morphllm.com/ai-coding-agent)
- Go binary = fast startup, low memory overhead — runs alongside your voice daemon without resource contention [lushbinary](https://lushbinary.com/blog/opencode-developer-guide-terminal-ai-coding-agent/)
- Active governance: SST team, clear roadmap, not a solo project [opencode](https://opencode.ai)

**Verdict relevance:** ✅ Prime candidate for the coding/execution "hands." The Plan mode + Ollama support perfectly matches your supervised-execution mental model.

***

### #18 · OpenAI Codex CLI — OpenAI's Terminal Agent
**What it is:** OpenAI's official open-source terminal coding agent, built in Rust, with configurable sandboxed execution and AGENTS.md memory. [vibecoding](https://vibecoding.gallery/en/tools/openai-codex-cli/)

**Stack & How it works:** Rust binary. The agent reads an `AGENTS.md` file in the repo for project-level instructions, and `.codex/skills/` for reusable skill definitions. Supports three approval modes: fully manual, semi-auto (approve commands), and full-auto (network-disabled sandbox). MCP servers plug in natively. Authenticates via ChatGPT account *or* BYOK API key. [developers.openai](https://developers.openai.com/codex/cli)

**Unique Points:**
- Rust binary — fastest startup, lowest overhead among all coding agents [vibecoding](https://vibecoding.gallery/en/tools/openai-codex-cli/)
- Full-auto mode runs with network disabled in a sandboxed directory — strongest local safety default [youtube](https://www.youtube.com/watch?v=FUq9qRwrDrI)
- AGENTS.md + `.codex/skills` pattern is directly analogous to your SKILL.md Agent Skills format [github](https://github.com/openai/codex/blob/main/AGENTS.md)
- Officially maintained by OpenAI — best aligned with latest GPT model capabilities [developers.openai](https://developers.openai.com/codex/cli)

**Verdict relevance:** Architecturally excellent, but cloud-tuned (GPT models). For your fully-local goal, OpenCode with Ollama is more native. Keep as a reference for the AGENTS.md/skills file pattern.

***

### #19 · Open Interpreter — NL → Local Code Execution
**What it is:** The original "ChatGPT Code Interpreter but local" — now in a Rust/Codex-fork rewrite with bubblewrap/seccomp sandboxing and Ollama provider support. [docs.openinterpreter](https://docs.openinterpreter.com/guides/running-locally)

**Stack & How it works:** Legacy Python version (unmaintained) let the LLM write and run Python/shell code directly. The new Rust version implements a Codex-style agent loop with Linux-native `bubblewrap` sandboxing and `seccomp` syscall filtering — the strongest sandboxing on Linux in this list. Supports Ollama and other local providers. [docs.clore](https://docs.clore.ai/guides/ai-platforms-and-agents/open-interpreter)

**Unique Points:**
- `bubblewrap` + `seccomp` sandbox — Linux kernel-native isolation, no Docker overhead [docs.openinterpreter](https://docs.openinterpreter.com/guides/running-locally)
- 63K+ stars community means abundant how-to guides, plugins, and integration examples [docs.clore](https://docs.clore.ai/guides/ai-platforms-and-agents/open-interpreter)
- The "01" voice project is dead (Nov 2024), but the core execution engine is healthy [docs.openinterpreter](https://docs.openinterpreter.com/guides/running-locally)
- Natural language → code execution is the most general "hands" capability [docs.clore](https://docs.clore.ai/guides/ai-platforms-and-agents/open-interpreter)

**Verdict relevance:** ✅ The bubblewrap/seccomp approach is perfect for Wayland's security model. Strong candidate for sandboxed execution layer.

***

### #20 · Cline — VS Code Agent + Headless CLI
**What it is:** An autonomous coding agent SDK available as VS Code extension, JetBrains plugin, and headless CLI — trusted by 8M+ developers. [deployhq](https://www.deployhq.com/guides/cline)

**Stack & How it works:** TypeScript. Operates in **Plan mode** (LLM reads codebase and proposes a plan — no file writes) and **Act mode** (executes the approved plan). The CLI supports parallel agents for CI/CD pipelines. BYOK any model including Ollama. Rules files are auto-loaded from the project directory. [github](https://github.com/cline/cline)

**Unique Points:**
- Plan/Act separation is the gold standard for supervised agentic coding [cline](https://cline.bot)
- 8M+ developer installs — the most widely adopted agent in this list [cline](https://cline.bot)
- Headless CLI enables CI/CD integration and scripted invocation from your voice assistant [linkedin](https://www.linkedin.com/posts/sarthaksharma14_you-can-now-run-ai-coding-agents-directly-activity-7428789548563976192-hmPL)
- SDK architecture means you can embed Cline's agent loop into your own application [github](https://github.com/cline/cline)

**Verdict relevance:** ✅ The SDK + headless CLI makes Cline the most *embeddable* coding agent. Your voice assistant can call `cline --headless` as a subprocess and get back structured results.

***

### #21 · Goose — Linux Foundation / AAIF General-Purpose Agent
**What it is:** A general-purpose Rust agent under the Linux Foundation's AAIF, positioned as "the Firefox of agents" — the reference implementation for MCP. [github](https://github.com/aaif-goose/goose)

**Stack & How it works:** Rust CLI + desktop app + embeddable API. Goose was the *first* public MCP client (November 2024) and is the canonical reference implementation for new MCP spec features. It supports 15+ LLM providers including Ollama. A Lead/Worker model lets a powerful frontier model plan while a cheaper local model executes subtasks. Recipes (reusable agent workflows) are scheduled or triggered manually. [github](https://github.com/aaif-goose/goose/discussions/9173)

**Unique Points:**
- **First MCP client ever** — deepest MCP compatibility of any agent [github](https://github.com/aaif-goose/goose/discussions/6852)
- Lead/Worker model pattern: use a big cloud model for planning + Qwen3.5-4B locally for execution — *directly applicable to your 6GB VRAM budget* [github](https://github.com/aaif-goose/goose/discussions/6852)
- AAIF/Linux Foundation governance = not dependent on any company's survival [intuitionlabs](https://intuitionlabs.ai/articles/agentic-ai-foundation-open-standards)
- MCP Apps support: agents can return interactive web UIs embedded in responses [intuitionlabs](https://intuitionlabs.ai/articles/agentic-ai-foundation-open-standards)

**Verdict relevance:** ✅ **Top-tier candidate**. The Lead/Worker pattern is exactly right for your hardware. AAIF governance provides long-term stability. MCP-first design aligns with your tool integration plan.

***

### #22 · Aider — Git-Native Terminal Pair Programmer
**What it is:** A terminal-based AI pair programmer that auto-commits every change with conventional commit messages, treats your git repo as the workspace. [aider](https://aider.chat/docs/git.html)

**Stack & How it works:** Python. Aider sends file contents + your request to any LLM (including Ollama), receives a diff, applies it, and immediately runs `git commit` with co-authored-by trailers. Supports 12 edit formats (diff, whole-file, etc.) adapting to what each LLM handles best. The `--undo` flag rolls back the last commit instantly. [github](https://github.com/NousResearch/hermes-agent/issues/534)

**Unique Points:**
- Every AI edit is a reversible git commit — the safest agentic coding workflow [aider](https://aider.chat/docs/git.html)
- Edit format auto-selection: Aider benchmarks each LLM and picks the format it handles best [github](https://github.com/NousResearch/hermes-agent/issues/534)
- Works in any directory with a git repo — no project setup, no config files needed [youtube](https://www.youtube.com/watch?v=1_uqt9oK0IM)
- `--watch` mode monitors for AI-comment annotations in source files and auto-executes them [github](https://github.com/NousResearch/hermes-agent/issues/534)

**Verdict relevance:** ✅ Best choice for *code editing specifically*. The git-safety model is ideal for supervised execution. Narrow scope (coding only) means it's a composable component, not a full foundation.

***

### #23 · gptme — Terminal Agent, Local-First
**What it is:** A minimal terminal agent with shell, Python, file, and browser tools — explicitly local-first with Ollama/llama.cpp as primary targets and Kokoro TTS output. [youtube](https://www.youtube.com/watch?v=QBmFw7eiLPU)

**Stack & How it works:** Python. Tools are registered as Python functions and called by the LLM. A persistent session file saves conversation history, letting gptme resume long-running tasks. Browser use is available via a Playwright integration. TTS output via Kokoro (your verified TTS pick) lets it speak responses. The developer maintains an Ollama-compatible fork for local models. [aisengtech](https://aisengtech.com/2025/03/16/deploy-local-chatgpt-or-deepseek-like-application-using-gptme/)

**Unique Points:**
- Kokoro TTS output built in — same TTS as your verified stack, making integration trivial [youtube](https://www.youtube.com/watch?v=QBmFw7eiLPU)
- Persistent session files enable long multi-step tasks that survive restarts
- Minimal Python codebase (~4K stars) — readable enough to fork and customize [youtube](https://www.youtube.com/watch?v=QBmFw7eiLPU)
- `gptme-eval` benchmark suite included for testing agent task completion [aisengtech](https://aisengtech.com/2025/03/16/deploy-local-chatgpt-or-deepseek-like-application-using-gptme/)

**Verdict relevance:** ✅ The Kokoro TTS and Ollama-first design make it the most *drop-in compatible* terminal agent for your exact stack. Small codebase = easy to extend.

***

## Section C — Shell Helpers / Pipelines

***

### #24 · Fabric — AI Prompt-Pattern Framework
**What it is:** A Unix-pipeline-first AI augmentation framework with 240–250+ curated "Patterns" — reusable prompt templates for specific real-world tasks, piped together via stdin/stdout. [lobehub](https://lobehub.com/skills/danielmiessler-personal_ai_infrastructure-fabric)

**Stack & How it works:** Go CLI. Each Pattern is a markdown file defining a system prompt for a task (`summarize`, `extract_wisdom`, `analyze_claims`, `create_threat_model`, etc.). The `Chatter` struct routes to 150+ providers. You pipe any iipe any input into Fabric: `cat article.txt | fabric -p summarize`. The framework also runs as a REST API server for integration  [explainx](https://explainx.ai/skills/supercent-io/skills-template/fabric). Local Ollama models fully supported  [danielkossmann](https://www.danielkossmann.com/installing-fabric-open-source-ai-framework-ubuntu-linux/).

**Unique Points:**
- 240+ community-curated, task-specific prompt patterns — the largest open prompt library [explainx](https://explainx.ai/skills/supercent-io/skills-template/fabric)
- Unix pipe philosophy: composable with any tool that reads stdin/writes stdout [github](https://github.com/danielmiessler/fabric/blob/main/patterns/suggest_pattern/user.md)
- Works as REST API server — your voice assistant can call `POST /api/patterns/summarize` [deepwiki](https://deepwiki.com/danielmiessler/fabric)
- 42K+ stars signals enormous adoption [explainx](https://explainx.ai/skills/supercent-io/skills-template/fabric)

**Verdict relevance:** ✅ Not a voice assistant, but its Pattern library is a goldmine for your RAG procedure index. Map each Pattern to a SKILL.md entry — instant library of 240+ Agent Skills.

***

### #25 · ShellGPT — NL → Shell Command Generator
**What it is:** A command-line productivity tool that converts natural language to shell commands, with shell integration for Bash/ZSH (Ctrl+L to complete inline). [github](https://github.com/TheR1D/shell_gpt/releases)

**Stack & How it works:** Python CLI using litellm for provider abstraction. Three modes: default chat, `--shell` (generate + optionally execute a command), `--describe-shell` (explain what a command does). Shell integration hooks into your prompt buffer — type in natural language, press Ctrl+L, get the shell command inserted. Supports OpenAI-compatible APIs; local model support via litellm → Ollama. [github](https://github.com/TheR1D/shell_gpt)

**Unique Points:**
- Ctrl+L shell integration is the smoothest NL→shell UX in terminal — type naturally, convert in-place [newreleases](https://newreleases.io/project/github/TheR1D/shell_gpt/release/0.9.2)
- `--describe-shell` teaches as it goes — explains *why* a command works [github](https://github.com/TheR1D/shell_gpt)
- Inline command editing before execution — never blindly runs AI output [github](https://github.com/TheR1D/shell_gpt/releases)
- Active maintenance (updated to GPT-5.4-mini default in May 2026) [github](https://github.com/TheR1D/shell_gpt/releases)

**Verdict relevance:** ✅ Useful as a lightweight utility component. The `--shell` mode can be called from your voice assistant as a fast "what's the command for X?" sub-task. Not a foundation.

***

### #26 · AIChat — All-in-One Rust LLM CLI
**What it is:** A Rust CLI that packages chat-REPL, shell assistant, RAG with built-in vector DB, function calling, agent system, and a local Ollama-compatible server into one binary. [github](https://github.com/sigoden/aichat/wiki/RAG-Guide)

**Stack & How it works:** Rust binary. Supports 20+ providers directly (OpenAI, Claude, Gemini, Ollama, Groq, Mistral, Cloudflare, etc.). The RAG system builds knowledge bases from documents using a built-in vector DB + full-text engine with `reciprocal_rank_fusion` — no external Chroma/Qdrant needed. The agent system chains tools with function calling. [deepwiki](https://deepwiki.com/sigoden/aichat/1-overview)

**Unique Points:**
- Built-in vector DB + full-text search = RAG with zero external dependencies [github](https://github.com/sigoden/aichat/wiki/RAG-Guide)
- `reciprocal_rank_fusion` hybrid retrieval — same approach as your hierarchical RAG vision [github](https://github.com/sigoden/aichat/wiki/RAG-Guide)
- Rust binary + 20+ providers in one file — smallest deployment footprint for multi-provider routing [deepwiki](https://deepwiki.com/sigoden/aichat/1-overview)
- Can run as an Ollama-compatible local server — acts as a proxy for other tools [deepwiki](https://deepwiki.com/sigoden/aichat/1-overview)

**Verdict relevance:** ✅ The built-in RAG with hybrid search is uniquely valuable for your task-procedure knowledge base. Strong utility component; last major push was February 2026 (slight staleness concern). [github](https://github.com/sigoden/aichat)

***

### #27 · AI Shell — Simple NL → Shell CLI
**What it is:** A minimal npm CLI inspired by GitHub Copilot X CLI — type natural language, get a shell command with explanation. [github](https://github.com/BuilderIO/ai-shell)

**Stack & How it works:** Node.js/TypeScript. Sends the user's natural language query to OpenAI's chat completion API, parses a structured JSON response for `command` and `explanation`, presents them in the terminal, then asks: Run / Edit / Cancel. Chat mode, silent mode, and configurable OpenAI endpoint (can point to a local Ollama-compatible server). [ainews.nbshare](https://ainews.nbshare.io/post/8586259/ai-shell-a-cli-that-converts-natural-language-to-shell-commands/)

**Unique Points:**
- Zero-config: `npm install -g @builder.io/ai-shell` and go [builder](https://www.builder.io/blog/ai-shell)
- Run/Edit/Cancel flow prevents accidental execution [ainews.nbshare](https://ainews.nbshare.io/post/8586259/ai-shell-a-cli-that-converts-natural-language-to-shell-commands/)
- Chat mode supports multi-turn conversation for iterative command refinement [ainews.nbshare](https://ainews.nbshare.io/post/8586259/ai-shell-a-cli-that-converts-natural-language-to-shell-commands/)

**Verdict relevance:** ❌ Functionally redundant given ShellGPT, AIChat, and gptme which all do the same with more features and active maintenance. Last meaningful push January 2026. Cut from active consideration.

***

## Quick-Reference Comparison Table

| # | Project | Lang | Voice | Actions | Local LLM | Unique Edge | Keep? |
|---|---------|------|-------|---------|-----------|-------------|-------|
| 1 | Newelle | Python/GTK4 | ✅ | CLI approval | Ollama | GNOME-native Flatpak, MCP, extensions | ✅ |
| 2 | NyarchAssistant | Python/GTK4 | ✅ | CLI approval | Ollama | Live2D avatar, Voicevox | 🔶 persona only |
| 3 | Leon | Node/Python | ✅ | Skills | ❌ v2 WIP | Oldest ecosystem, TCP bridges | ⚠️ wait v2 |
| 4 | OVOS | Python | ✅ | Skills (intent-only) | Ollama persona | Best offline wake+STT+TTS | 🔶 pipeline ref |
| 5 | GLaDOS | Python | ✅ | MCP tools | Ollama | <600ms, barge-in, 6GB fits | ✅ |
| 6 | AgenticSeek | Python | ✅ | Multi-agent | Ollama | RTX 3060-optimized, web search | ✅ |
| 7 | QwenPaw | Python | Whisper | Skills+MCP | Ollama/llama.cpp | Proactive heartbeat, ReMe memory | ✅ |
| 8 | Row-Bot | Python | ✅ | Shell+browser | Ollama | Markdown knowledge graph | ✅ young |
| 9 | Moltis | Rust | ✅ 8+7 providers | Docker sandbox | Ollama | Single binary, strongest sandbox | ✅ |
| 10 | CAAL | Python/LiveKit | ✅ | n8n auto-MCP | Ollama | Self-modifying, n8n discovery | ✅ |
| 11 | RealtimeVoiceChat | Python | ✅ | ❌ chat only | Ollama | ~500ms, Docker, KoljaB libs | ✅ voice ref |
| 12 | LocalAGI | Go | TTS hooks | MCP+actions | LocalAI | No-code UI, agent teaming | ✅ |
| 13 | Seeva | Unknown | ❌ | ❌ | Unknown | Hotkey overlay UX | ❌ |
| 14 | azibom/assistant | Python/GTK4 | ❌ | Basic | Unknown | — | ❌ |
| 15 | Local-Voice | Python | ✅ | ❌ | Ollama | — | ❌ |
| 16 | OpenClaw | Node.js | ❌ | Shell+skills | Ollama | 700+ SKILL.md skills on ClawHub | ✅ skills |
| 17 | OpenCode | Go | ❌ | Build+Plan | Ollama | 75+ providers, LSP, Plan/Build | ✅ |
| 18 | Codex CLI | Rust | ❌ | Sandboxed | ❌ GPT | AGENTS.md, bubblewrap, official | 🔶 reference |
| 19 | Open Interpreter | Rust | ❌ | bubblewrap+seccomp | Ollama | Linux-native sandbox, 63K stars | ✅ |
| 20 | Cline | TypeScript | ❌ | Plan/Act+CLI | BYOK | 8M installs, SDK, headless | ✅ |
| 21 | Goose | Rust | ❌ | MCP+extensions | Ollama | First MCP client, Lead/Worker | ✅ |
| 22 | Aider | Python | ❌ | git-native | Ollama | Every change is a reversible commit | ✅ |
| 23 | gptme | Python | Kokoro out | Shell+browser | Ollama/llama.cpp | Same TTS stack as yours | ✅ |
| 24 | Fabric | Go | ❌ | Pattern pipeline | Ollama | 240+ patterns = instant SKILL library | ✅ |
| 25 | ShellGPT | Python | ❌ | Shell gen | Ollama | Ctrl+L inline, describe-shell | ✅ utility |
| 26 | AIChat | Rust | ❌ | Function calling | Ollama | Built-in hybrid RAG, zero deps | ✅ |
| 27 | AI Shell | Node.js | ❌ | Shell gen | ❌ OpenAI | — | ❌ |

***

## Prune Recommendation: Towards 2–3 Finalists

Based on the full analysis, the sharpest finalists for your specific hardware (RTX 3060 Mobile, 16GB RAM) and vision (GNOME/Wayland Siri with local voice loop + agentic executor + Agent Skills RAG) are:

**Voice Layer Foundation:** GLaDOS (#5) — its MCP-in-voice-loop architecture and <600ms on 6GB VRAM matches your constraints precisely. Strip the Portal persona, add your wake word via RealtimeSTT, swap Parakeet STT for Parakeet-local (already verified). [github](https://github.com/dnhkng/GLaDOS)

**Execution "Hands":** Goose (#21) — first MCP client, Lead/Worker pattern, Rust, Linux Foundation governance, Ollama support, and composable with any voice frontend. [github](https://github.com/aaif-goose/goose)

**Skills/Memory/RAG Substrate:** OpenClaw (#16) / AIChat (#26) hybrid — OpenClaw's 700+ ClawHub SKILL.md skills give you an instant Agent Skills library; AIChat's built-in hybrid-RAG vector DB (no Chroma/Qdrant to run) is the lightest way to index those skills for retrieval. [reddit](https://www.reddit.com/r/AI_Agents/comments/1r2u356/best_openclaw_skills_you_should_install_from/)