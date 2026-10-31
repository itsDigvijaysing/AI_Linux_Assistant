# Security

Phoenix (v3) is a **local-first, single-user** voice assistant for one person's own Ubuntu GNOME desktop.
This file says what it guarantees, how actions are controlled, and what risk remains. Every point names the
code that does it, so it can be checked.

## Threat model
- **In scope:** the assistant never raises its privileges, never opens a network service, never leaks your
  tokens, and refuses clearly catastrophic commands, also when a spoken request or text it reads (a web page,
  a file, the screen) tries to trick it.
- **Out of scope:** a shared or multi-user machine, and an attacker who already runs code as you.

## What is guaranteed
- **No sudo at runtime.** The app and the engine run as you; nothing calls `sudo`, `pkexec` or setuid
  helpers. System packages (PipeWire tools, `brightnessctl`, ...) are installed by you, once, with `apt`.
- **Your voice and conversations stay here.** Speech recognition (Parakeet), the voices (SuperTonic), the
  small router (jiwo) and the big model (Ollama) all run on this computer. Ollama is Phoenix's own server on
  `127.0.0.1:11435`; Phoenix itself listens on no port.
- **The internet only when you turn it on.** Model downloads; Web Access (weather from Open-Meteo, look-ups
  from DuckDuckGo and Wikipedia, `engine/web.py`, fixed https hosts, off by default); Todoist once you save a
  token (`engine/todoist.py`, `api.todoist.com` only); MCP servers you add. Web searches you ask for open in
  your browser. OpenVINO's analytics sender is blocked inside the process (`engine/jiwo.py`).
- **Tokens never sit in a file.** The Todoist token is kept in your login keyring through the freedesktop
  Secret Service (`secret_store.py`); the settings file only records that one is saved. The app passes it to
  the engine over a private pipe, where it stays in memory and is never logged.
- **Private files.** Settings, Knowledge, History (SQLite) and the paste portal's token are files only you can
  read (`0600`); their folders and the timers' folder are `0700`.

## How actions are controlled
1. **Action gate** (`safety/gate.py`). Tools that change the computer (`desktop.*`, `shell.run_command`, and
   every MCP tool, `mcp.*`), and adding or finishing a Todoist task (reading your tasks stays on), run only
   while actions are allowed (Safety page, `actions_armed`). Unattended
   automation can never run them (`autonomy` hard floor).
2. **You confirm the risky ones.** A shell command is always shown in full and needs a yes, on screen or by
   voice (`confirm_shell`). Each MCP server asks before every use unless you turn that off for it. Finishing a
   Todoist task asks "Mark ... as done?". When the router is unsure what you meant (50 to 85 %), it asks
   "Did you mean ...?" and acts only on a yes (`engine/brain.py`, `ASK_P`).
3. **Catastrophic-command blocklist** (`safety/denylist.py`), applied in the one place every command runs
   (`safety/executor.py`), whoever wrote the command: recursive delete or `chmod`/`chown` of `/`, `/home` or
   your home (also through quotes, globs, `cd` chains, simple variables, `find ... -delete` and
   `python -c` rmtree), writing to raw disks (`dd`, `mkfs`, `wipefs`, `shred`, redirects), fork bombs, and
   piping a download or decoded payload into a shell. It is a safety net, not a sandbox (below).
4. **Typed tools, not shell strings.** Volume, brightness, apps, folders and media run fixed argument lists
   (no shell). Apps open only through their installed `.desktop` entry, so a request can't start `poweroff`.
   "Write a reply" pastes text but never presses Send.
5. **Resource limits.** Commands run in a `systemd-run --user --scope` with `MemoryMax=2G`, `TasksMax=512`
   and a time limit (20 s by default), which stops fork bombs and runaway memory.
6. **Prompt-injection framing.** The model is told that text from tools, files, web pages or the screen is
   data, never instructions, and never to run commands that delete or damage the system.
7. **Fewer tools, fewer mistakes.** Plain chat gets no action tools at all; MCP servers' tools can be switched
   off one by one so only the ones you use reach the model.

### Why there is no OS sandbox (evaluated 2026-07-05; revisit only if the threat model changes)
Filesystem and namespace sandboxes (bubblewrap, firejail, Landlock, `ProtectHome=`) would break the desktop
tools, which need your live session (Wayland, PipeWire, D-Bus, `gsettings`, launching apps), and a process with
the session bus can escape them anyway (for example by asking systemd to start an unconfined unit). The
resource limits above are what is kernel-enforced.

## Residual risk
With actions allowed, the model can control the desktop and, after your yes, run shell commands. A clever
prompt injection (spoken, or text on screen or in a file it reads) could try a harmful but non-catastrophic
action; the gate, confirmations, blocklist and framing make that harder, not impossible.
- Turn actions off on the Safety page when you only want answers.
- Read a shell command before saying yes; keep "Ask Before Each Use" on for MCP servers.
- Only add MCP servers you trust: a server can do anything its program can.

## Reporting
This is a personal project; open an issue describing the concern (never include tokens or secrets).
