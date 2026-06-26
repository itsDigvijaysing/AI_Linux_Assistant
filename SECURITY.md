# Security

AI Linux Assistant is a **local-first, single-user** desktop voice assistant. This document states its
threat model, what's guaranteed, and the safeguards.

## Threat model
- **Intended environment:** one trusted user on their own Linux machine (Ubuntu/GNOME/Wayland).
- **In scope:** the assistant should never silently escalate privileges, never expose a network service,
  never leak secrets, and should refuse obviously-catastrophic actions.
- **Out of scope:** a multi-tenant/shared host, or defending against the logged-in user attacking
  themselves. A determined adversary who already runs code as your user is not contained.

## What's guaranteed
- **No superuser at runtime.** The running assistant never calls `sudo`/`root` — every command runs as
  your user. (Verified: there is no `sudo`/`pkexec`/`setuid` in the runtime code path.)
- **The only privileged step is `./ai-linux setup`**, which does a one-time `apt` install of helper tools
  (`brightnessctl`, `playerctl`, `gnome-screenshot`, `wl-clipboard`, `ydotool`), adds you to the `video`
  group (brightness), and installs a scoped **udev rule** so `ydotool` can use `/dev/uinput` via a
  per-session ACL — deliberately **not** the `input` group, so no process gains system-wide keystroke
  read (which would defeat Wayland's input isolation). Package names are hardcoded (no injection).
- **No inbound network.** No listening socket is opened. Ollama is reached only on `127.0.0.1:11434`;
  an optional cloud brain (Groq/OpenAI-compatible) is outbound HTTPS. MCP tool servers use stdio.
- **No secrets in the repo.** API keys are read from environment variables only (`GROQ_API_KEY` /
  `GLADOS_API_KEY` / …); configs ship `api_key: null`.
- **User-private state.** Runtime/IPC files (`$XDG_RUNTIME_DIR/ai-linux/`) and `data/` are created `0700`;
  TTS temp files use unpredictable names and are deleted after playback.

## How actions are controlled
1. **Action gate** (`core/tool_safety.py`): `mcp.shell.*` and `mcp.computer_use.*` are **denied by default**
   and run only when the session is *armed* (`GLADOS_ALLOW_ACTIONS=1`). Interactive `./ai-linux` arms them
   so the assistant can act; `./ai-linux --no-actions` runs it disarmed (chat/info only).
2. **Autonomy hard-floor:** the autonomous loop can **never** run gated actions, regardless of env/config.
3. **Destructive-command denylist** (`mcp/shell_server.py`): a conservative backstop that refuses clearly
   catastrophic commands (`rm -rf /`/`~`/`$HOME`/`/home`, `dd of=/dev/…`, `mkfs`/`wipefs`/`shred`, redirect
   to a raw disk, fork bomb, `chmod/chown -R /`, `curl … | sh`) **regardless of how the command was
   produced** (model, skill, or learned skill). It is a safety net, not a sandbox.
4. **Prompt-injection mitigation:** the system prompt instructs the model to treat tool/file/web/screenshot
   text as untrusted *data*, never instructions, and to refuse data-destroying actions.

## Residual risk & recommendations
When armed, the LLM can run shell commands and control the desktop — that's the point of an assistant, but
it means a sufficiently clever **prompt injection** (spoken, or text in a file/screenshot it reads) could
attempt a harmful action. The denylist + gate + prompt framing reduce this; they don't eliminate it.
- Prefer **`qwen3:4b`** (or a cloud brain) over `qwen3:1.7b` for better injection resistance.
- Run **`./ai-linux --no-actions`** when you only need chat/answers.
- Learned skills (`skills/learned/`, from `save_skill`) are markdown only and never execute on their own;
  their commands still pass the gate + denylist when run.

## Reporting
This is a personal project; open an issue describing the concern (do not include secrets).
