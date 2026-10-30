# Phoenix (AI Linux Assistant)

Phoenix is a local voice assistant for Ubuntu GNOME (Wayland). You talk to it, it talks back, and it does simple desktop jobs:
opening apps and files, changing volume or brightness, searching the web, reading the screen, answering questions.
Everything runs on your own computer: speech recognition, the language models and the voice. No account, no API key,
no cloud.

It is built for an ordinary gaming laptop: an RTX 3060 Mobile with 6 GB of video memory, an Intel Iris Xe iGPU,
16 GB of RAM. On battery a small model on the Intel GPU handles most commands so the NVIDIA GPU can sleep.

![AI Linux Assistant](asset/AI_Linux_Assitant.webp)

Demo video (v2, the older GNOME Shell extension version): https://www.youtube.com/watch?v=2Qq7Jslxo6Y

## Features

- **One native app.** GTK4 and libadwaita, no browser view and no Shell extension. A tray icon, a small floating
  status pill, and a global shortcut (Super+Alt+Space) to talk.
- **Listening modes:** wake word (default "Phoenix", which is also its name), shortcut only, always listening, or off.
- **Battery and charger profiles.** The models are picked once. On battery, a small decision model on the Intel GPU
  picks the command; anything it is unsure of, and all chat, goes to the big model on the NVIDIA GPU. On the charger
  the big model does everything by default. Each profile has its own Speed and can turn the small model on or off.
- **Safe actions.** Every action that changes something goes through one gate: catastrophic commands are blocked,
  risky ones ask first (a dialog, or a spoken yes or no). No sudo at runtime, ever.
- **Tools you can extend.** Built-in desktop tools plus any MCP server you add in Tools.
- **Screen reading.** The big model can look at a screenshot to answer "what's on my screen".
- **Live words.** What you say appears as you speak, in the pill and on Home; the reply shows as it is spoken.
- **Knowledge.** Facts you keep for good, plus facts it learns from conversations that fade after 14 days. Say
  "remember that..." or "forget that...", or edit them in the Knowledge window.
- **Speed** on Home, one row: Save Battery (the big model loads when needed, freed after 2 minutes), Automatic (kept
  30 minutes) or Max Performance (stays loaded). The pill shows when it is starting and when the big model takes over.
- **History** of every turn, searchable, kept on disk only. **New Session** on Home starts a fresh conversation.
- **Health page** that checks every part (audio, echo cancellation, GPUs, Ollama, models, shortcut, tray) and says how to fix what is missing.

## Models

| Role | Default | Runs on | Alternatives |
|---|---|---|---|
| Big model: chat, tools, vision | Qwen3.5 4B (Ollama `qwen3.5:4b`) | NVIDIA | Gemma 4 E4B |
| Small model: battery-mode command picking | jiwo 0.8B, 8-bit OpenVINO ([itsDigvijaysing/jiwo-0.8b-openvino-int8](https://huggingface.co/itsDigvijaysing/jiwo-0.8b-openvino-int8)) | Intel GPU or CPU | Laya ([itsDigvijaysing/laya-typed-decisions-openvino](https://huggingface.co/itsDigvijaysing/laya-typed-decisions-openvino)), or none |
| Speech recognition | Parakeet TDT 0.6B (ONNX) | CPU | Parakeet CTC 110M |
| Voice | SuperTonic (ONNX) | CPU | other SuperTonic voices |

How they were chosen, with every measurement: [bench/README.md](bench/README.md).

## Requirements

- Ubuntu 26.04 (GNOME, Wayland) or similar, with system Python 3.14 and PyGObject:
  `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` (optional: `gir1.2-gtk4layershell-1.0` for the pill).
- [Ollama](https://ollama.com) 0.40 or newer for the big model (Gemma 4 needs 0.40).
- An NVIDIA GPU with 6 GB or more for the big model. The small model runs on an Intel GPU or any x86-64 CPU.
- PipeWire (`pw-play`, `pw-record`) for sound.

## Quick start

```bash
git clone https://github.com/itsDigvijaysing/AI_Voice_Assistant.git
cd AI_Voice_Assistant

# the speech engine's own environment (the GTK app itself uses system Python)
python3 -m venv ~/.local/share/ai-linux/venv
~/.local/share/ai-linux/venv/bin/pip install -r requirements-engine.txt

bin/ai-linux-app

# optional: add it to the app grid (user-level, no sudo; "uninstall" removes it)
PYTHONPATH=src python3 -m ai_linux.desktop_entry install
```

The first start walks through the microphone, the models and the listening mode. Models download from the Models page
(big models through Ollama, the small model and speech models from Hugging Face and GitHub, with checksums).

Other ways to start: `bin/ai-linux-app --background` (tray only), `--demo` (simulated turns, no microphone),
`--toggle`, `--mute`, `--unmute`, `--quit` (sent to the running app).

## How it is built

```
GTK4 app (system Python)                    engine child process (venv)
  window, tray, pill, hotkey   <-- JSON -->   mic -> VAD -> Parakeet -> router -> tools / big model -> SuperTonic
  settings, history, health                   router: small model (Intel GPU) or big model (NVIDIA, Ollama)
```

- `src/ai_linux/`: the app. `engine/` is the speech loop, `safety/` the action gate and denylist, `ui/` the pages.
- Design, contracts and open work: [src/ai_linux/ARCHITECTURE.md](src/ai_linux/ARCHITECTURE.md).
- Security model: [SECURITY.md](SECURITY.md).
- `src/glados/` is the older v2 engine, kept only as the source the safety code was ported from.

## Development

- Tests: `/usr/bin/python3 -m unittest discover -s tests`. Parts that need GTK, OpenVINO or a downloaded model skip
  themselves when those are missing.
- See a page without touching your desktop:
  `scripts/headless-run.sh bin/ai-linux-app --screenshot /tmp/out.png --page models`
- Benchmarks and model exports: `bench/router/`.

## Status

v3 is a full rewrite as one desktop app. Working and tested: the app, engine, profiles, models, safety gate, MCP
tools, history, knowledge and health checks. Work in progress and open items: `HANDOFF.md`. Still to do: a long live voice run on the real desktop, and an installer.

## License

MIT. See [LICENSE](LICENSE). The models keep their own licences (Apache-2.0 for Qwen3.5, Gemma 4, jiwo and Laya;
CC-BY-4.0 for Parakeet). Thanks to the GLaDOS project (MIT), whose engine v2 was built on.
